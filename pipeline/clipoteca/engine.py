"""Bounded four-language discovery with isolation and local-first deduplication."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .catalog import Catalog, canonical_tokens
from .place_names import query_names
from .providers.base import (
    Adapter,
    AuthRequired,
    Candidate,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
)
from .translate.expand import QueryExpansion, expand_query


@dataclass(frozen=True)
class Execution:
    provider: str
    language: str
    term: str
    status: SupportStatus
    found: int
    elapsed_seconds: float
    error: str = ""


@dataclass
class SearchResult:
    expansion: QueryExpansion
    local: list[dict]
    candidates: list[Candidate]
    executions: list[Execution]
    elapsed_seconds: float
    duplicates: int = 0
    duplicate_ids: list[str] = field(default_factory=list)


@dataclass
class CircuitBreaker:
    failures: dict[str, int] = field(default_factory=dict)
    open_after: int = 3
    cooldown_seconds: float = 60.0
    opened_at: dict[str, float] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic

    def open(self, provider: str) -> bool:
        opened = self.opened_at.get(provider)
        if opened is not None and self.clock() - opened >= self.cooldown_seconds:
            self.failures[provider] = 0
            self.opened_at.pop(provider, None)
        return self.failures.get(provider, 0) >= self.open_after

    def record(self, provider: str, succeeded: bool) -> None:
        if succeeded:
            self.failures[provider] = 0
            self.opened_at.pop(provider, None)
        else:
            count = self.failures.get(provider, 0) + 1
            self.failures[provider] = count
            if count >= self.open_after:
                self.opened_at.setdefault(provider, self.clock())


def _local_hits(catalog: Catalog, query: str) -> list[dict]:
    tokens = canonical_tokens(query)
    hits = []
    for clip in catalog.clips:
        if clip.get("status") != "published":
            continue
        text = [
            *clip["title"].values(),
            *clip["description"].values(),
            *clip["tags"].get("es", []),
            *clip["tags"].get("en", []),
            clip["source"].get("source_title", ""),
        ]
        index = set().union(*(canonical_tokens(str(piece)) for piece in text))
        if tokens and tokens <= index:
            hits.append(clip)
    return hits


class SearchEngine:
    def __init__(
        self,
        adapters: Sequence[Adapter],
        *,
        concurrency: int = 16,
        discovery_seconds: float = 15.0,
        per_source_seconds: float = 6.0,
        max_pages: int = 2,
        max_candidates: int = 100,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            concurrency < 1
            or discovery_seconds <= 0
            or per_source_seconds <= 0
            or max_candidates < 1
        ):
            raise ValueError("invalid discovery limits")
        self.adapters = list(adapters)
        self.concurrency = concurrency
        self.discovery_seconds = discovery_seconds
        self.per_source_seconds = per_source_seconds
        self.max_pages = max_pages
        self.max_candidates = max_candidates
        self.breaker = breaker or CircuitBreaker()
        self.clock = clock

    async def search(
        self,
        query: str,
        *,
        catalog: Catalog | None = None,
        expansion: QueryExpansion | None = None,
        allow_partial_expansion: bool = False,
    ) -> SearchResult:
        catalog = catalog or Catalog()
        expansion = expansion or expand_query(query)
        start = self.clock()
        local = _local_hits(catalog, query)
        if not allow_partial_expansion and (not expansion.complete or expansion.errors):
            return SearchResult(expansion, local, [], [], self.clock() - start)
        sem = asyncio.Semaphore(self.concurrency)
        provider_locks = {adapter.name: asyncio.Lock() for adapter in self.adapters}
        limits = SearchLimits(
            self.max_pages,
            self.max_candidates,
            self.per_source_seconds,
            tuple(sorted(query_names([query]))),
        )
        languages = ("es", "en", "zh", "ru")
        completed: dict[tuple[str, str], tuple[list[Candidate], SupportStatus, str, float]] = {}

        # One worker per provider, four languages in series. This prevents
        # rate-limit bursts while allowing other providers to make progress.
        # Credentials known absent at scheduling time never acquire a slot.
        ordered_adapters = sorted(
            self.adapters,
            key=lambda adapter: (
                adapter.support_status != SupportStatus.SUPPORTED_SEARCH_DOWNLOAD,
                -int(getattr(adapter, "discovery_priority", 0)),
                adapter.name == "youtube",
            ),
        )
        active: list[tuple[Adapter, list[tuple[str, str]]]] = []
        for adapter in ordered_adapters:
            terms = [
                (lang, expansion.terms[lang])
                for lang in languages
                if expansion.terms.get(lang) and lang in adapter.accepts_text_languages
            ]
            if not terms:
                continue
            if adapter.support_status in (SupportStatus.UNAVAILABLE, SupportStatus.AUTH_REQUIRED):
                reason = (
                    "auth_required"
                    if adapter.support_status == SupportStatus.AUTH_REQUIRED
                    else getattr(adapter, "unavailable_reason", "provider_unavailable")
                )
                for lang, _ in terms:
                    completed[(adapter.name, lang)] = ([], adapter.support_status, reason, 0.0)
            else:
                active.append((adapter, terms))

        async def execute_one(adapter: Adapter, lang: str, term: str):
            started = self.clock()
            timeout = max(
                self.per_source_seconds,
                float(getattr(adapter, "search_timeout_seconds", 0)),
            )
            try:
                found = await asyncio.wait_for(adapter.search(term, lang, limits), timeout=timeout)
                self.breaker.record(adapter.name, True)
                return (
                    found[: limits.max_candidates],
                    adapter.support_status,
                    "",
                    self.clock() - started,
                )
            except RateLimited:
                return [], SupportStatus.RATE_LIMITED, "rate_limited", self.clock() - started
            except AuthRequired:
                return [], SupportStatus.AUTH_REQUIRED, "auth_required", self.clock() - started
            except asyncio.TimeoutError:
                self.breaker.record(adapter.name, False)
                return [], SupportStatus.TIMEOUT, "provider_timeout", self.clock() - started
            except ProviderError as exc:
                self.breaker.record(adapter.name, False)
                return [], exc.status, type(exc).__name__, self.clock() - started
            except Exception as exc:
                self.breaker.record(adapter.name, False)
                return [], SupportStatus.UNAVAILABLE, type(exc).__name__, self.clock() - started

        async def execute_provider(adapter: Adapter, terms: list[tuple[str, str]]):
            async with provider_locks[adapter.name]:
                # Preserve the breaker across requests, but do not let three
                # failures within THIS request suppress its fourth language.
                # An already-open breaker skips the entire provider upfront.
                if self.breaker.open(adapter.name):
                    for lang, _ in terms:
                        completed[(adapter.name, lang)] = (
                            [],
                            SupportStatus.UNAVAILABLE,
                            "circuit_open",
                            0.0,
                        )
                    return
                # YouTube is best-effort discovery only: never occupies a
                # media-provider slot and never retains the completed round.
                if adapter.name == "youtube":
                    for lang, term in terms:
                        completed[(adapter.name, lang)] = await execute_one(adapter, lang, term)
                else:
                    async with sem:
                        for lang, term in terms:
                            completed[(adapter.name, lang)] = await execute_one(adapter, lang, term)

        tasks = {
            asyncio.create_task(execute_provider(adapter, terms)): adapter.name
            for adapter, terms in active
        }
        blocking = {task for task, name in tasks.items() if name != "youtube"}
        watched = blocking or set(tasks)
        # Upper bound covers the worst case: all provider calls time out
        # individually, in ceiling(N/concurrency) waves. Unlike the former
        # fixed 15s deadline, queued languages receive their own attempt.
        non_youtube = [(a, terms) for a, terms in active if a.name != "youtube"]
        waves = math.ceil(len(non_youtube) / self.concurrency)
        slowest_provider_budget = max(
            (
                sum(
                    max(
                        self.per_source_seconds,
                        float(getattr(adapter, "search_timeout_seconds", 0)),
                    )
                    for _ in terms
                )
                for adapter, terms in non_youtube
            ),
            default=0.0,
        )
        hard_cap = min(
            180.0,
            max(self.discovery_seconds, waves * slowest_provider_budget + 12.0),
        )
        try:
            if watched:
                await asyncio.wait(watched, timeout=hard_cap)
        finally:
            pending = {task for task in tasks if not task.done()}
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        executions: list[Execution] = []
        source_candidates: dict[str, deque[Candidate]] = {}
        for adapter in ordered_adapters:
            for lang in languages:
                term = expansion.terms.get(lang)
                if not term or lang not in adapter.accepts_text_languages:
                    continue
                entry = completed.get((adapter.name, lang))
                if entry is None:
                    entry = (
                        [],
                        SupportStatus.TIMEOUT,
                        "youtube_nonblocking"
                        if adapter.name == "youtube"
                        else "discovery_hard_cap",
                        hard_cap,
                    )
                found, status, error, elapsed = entry
                executions.append(
                    Execution(
                        adapter.name,
                        lang,
                        term,
                        status,
                        len(found),
                        max(0.0, elapsed),
                        error,
                    )
                )
                source_candidates.setdefault(adapter.name, deque()).extend(found)

        # Interleave providers BEFORE applying the global candidate cap.
        # Otherwise one high-volume provider consumes all 100 slots.
        seen = {
            f"{clip['source']['provider']}:{clip['source']['provider_id']}": clip["id"]
            for clip in catalog.clips
            if clip.get("status") != "retired"
        }
        candidates: list[Candidate] = []
        duplicates = 0
        duplicate_ids: set[str] = set()
        for _ in range(max((len(q) for q in source_candidates.values()), default=0)):
            for adapter in ordered_adapters:
                queue = source_candidates.get(adapter.name)
                if not queue:
                    continue
                candidate = queue.popleft()
                if catalog.is_retired_source(candidate.source_url):
                    continue
                if candidate.origin_id in seen:
                    duplicates += 1
                    if seen[candidate.origin_id]:
                        duplicate_ids.add(seen[candidate.origin_id])
                    continue
                seen[candidate.origin_id] = ""
                if len(candidates) < self.max_candidates:
                    candidates.append(candidate)
        return SearchResult(
            expansion,
            local,
            candidates,
            executions,
            self.clock() - start,
            duplicates,
            sorted(duplicate_ids),
        )
