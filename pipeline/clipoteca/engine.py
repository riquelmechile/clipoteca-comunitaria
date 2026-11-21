"""Bounded four-language discovery with isolation and local-first deduplication."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .catalog import Catalog, canonical_tokens
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
        concurrency: int = 4,
        discovery_seconds: float = 15.0,
        per_source_seconds: float = 6.0,
        max_pages: int = 2,
        max_candidates: int = 100,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1 or discovery_seconds <= 0 or max_candidates < 1:
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
    ) -> SearchResult:
        catalog = catalog or Catalog()
        expansion = expansion or expand_query(query)
        start = self.clock()
        local = _local_hits(catalog, query)
        if not expansion.complete or expansion.errors:
            return SearchResult(expansion, local, [], [], self.clock() - start)
        sem = asyncio.Semaphore(self.concurrency)
        provider_locks = {adapter.name: asyncio.Lock() for adapter in self.adapters}
        limits = SearchLimits(self.max_pages, self.max_candidates, self.per_source_seconds)
        tasks: dict[asyncio.Task, tuple[str, str, str]] = {}

        async def one(adapter: Adapter, language: str, term: str):
            # Acquire per-provider slot BEFORE global semaphore to prevent starvation.
            async with provider_locks[adapter.name]:
                async with sem:
                    started = self.clock()
                    if adapter.support_status == SupportStatus.UNAVAILABLE:
                        return (
                            [],
                            SupportStatus.UNAVAILABLE,
                            getattr(adapter, "unavailable_reason", "provider_unavailable"),
                            0.0,
                        )
                    if self.breaker.open(adapter.name):
                        return [], SupportStatus.UNAVAILABLE, "circuit_open", 0.0
                    try:
                        candidates = await asyncio.wait_for(
                            adapter.search(term, language, limits),
                            timeout=self.per_source_seconds,
                        )
                        self.breaker.record(adapter.name, True)
                        return (
                            candidates[: limits.max_candidates],
                            adapter.support_status,
                            "",
                            self.clock() - started,
                        )
                    except RateLimited:
                        # HTTP 429 does not trip the outage circuit breaker.
                        return (
                            [],
                            SupportStatus.RATE_LIMITED,
                            "rate_limited",
                            self.clock() - started,
                        )
                    except AuthRequired:
                        return (
                            [],
                            SupportStatus.AUTH_REQUIRED,
                            "auth_required",
                            self.clock() - started,
                        )
                    except asyncio.TimeoutError:
                        self.breaker.record(adapter.name, False)
                        return [], SupportStatus.TIMEOUT, "provider_timeout", self.clock() - started
                    except ProviderError as exc:
                        self.breaker.record(adapter.name, False)
                        return [], exc.status, type(exc).__name__, self.clock() - started
                    except Exception as exc:
                        self.breaker.record(adapter.name, False)
                        return (
                            [],
                            SupportStatus.UNAVAILABLE,
                            type(exc).__name__,
                            self.clock() - started,
                        )

        # Terms are queued round-robin by language, never four per provider first.
        ordered_adapters = sorted(
            self.adapters,
            key=lambda adapter: (
                adapter.support_status != SupportStatus.SUPPORTED_SEARCH_DOWNLOAD,
                adapter.name == "youtube",
            ),
        )
        for lang in ("es", "en", "zh", "ru"):
            for adapter in ordered_adapters:
                term = expansion.terms.get(lang)
                if term and lang in adapter.accepts_text_languages:
                    task = asyncio.create_task(one(adapter, lang, term))
                    tasks[task] = (adapter.name, lang, term)
        # YouTube Data API is discovery-only. Never hold a completed round
        # hostage to its slow requests; harvest only already-finished results.
        blocking = {task for task, (name, _, _) in tasks.items() if name != "youtube"}
        deadline_tasks = blocking or set(tasks)
        if deadline_tasks:
            await asyncio.wait(deadline_tasks, timeout=self.discovery_seconds)
        finished = {task for task in tasks if task.done()}
        pending = set(tasks) - finished
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        executions = []
        seen = {
            f"{clip['source']['provider']}:{clip['source']['provider_id']}": clip["id"]
            for clip in catalog.clips
        }
        candidates = []
        duplicate_ids: set[str] = set()
        duplicates = 0
        # Prioritize actual download adapters when the global quota fills.
        ordered_tasks = sorted(
            tasks,
            key=lambda task: (
                tasks[task][0]
                not in {
                    adapter.name
                    for adapter in self.adapters
                    if adapter.support_status == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
                },
                tasks[task][0] == "youtube",
            ),
        )
        for task in ordered_tasks:
            provider, language, term = tasks[task]
            if task in pending:
                found, status, error, elapsed = (
                    [],
                    SupportStatus.TIMEOUT,
                    "discovery_deadline",
                    self.discovery_seconds,
                )
            else:
                found, status, error, elapsed = task.result()
            executions.append(
                Execution(
                    provider,
                    language,
                    term,
                    status,
                    len(found),
                    max(0.0, elapsed),
                    error,
                )
            )
            for candidate in found:
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
