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


@dataclass
class CircuitBreaker:
    failures: dict[str, int] = field(default_factory=dict)
    open_after: int = 3

    def open(self, provider: str) -> bool:
        return self.failures.get(provider, 0) >= self.open_after

    def record(self, provider: str, succeeded: bool) -> None:
        if succeeded:
            self.failures[provider] = 0
        else:
            self.failures[provider] = self.failures.get(provider, 0) + 1


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
        sem = asyncio.Semaphore(self.concurrency)
        limits = SearchLimits(self.max_pages, self.max_candidates, self.per_source_seconds)
        tasks: dict[asyncio.Task, tuple[str, str, str]] = {}

        async def one(adapter: Adapter, language: str, term: str):
            async with sem:
                if self.breaker.open(adapter.name):
                    return [], SupportStatus.UNAVAILABLE, "circuit_open"
                try:
                    candidates = await asyncio.wait_for(
                        adapter.search(term, language, limits),
                        timeout=self.per_source_seconds,
                    )
                    self.breaker.record(adapter.name, True)
                    return candidates[: limits.max_candidates], adapter.support_status, ""
                except RateLimited:
                    self.breaker.record(adapter.name, False)
                    return [], SupportStatus.RATE_LIMITED, "rate_limited"
                except AuthRequired:
                    return [], SupportStatus.AUTH_REQUIRED, "auth_required"
                except asyncio.TimeoutError:
                    self.breaker.record(adapter.name, False)
                    return [], SupportStatus.TIMEOUT, "provider_timeout"
                except ProviderError as exc:
                    self.breaker.record(adapter.name, False)
                    return [], exc.status, type(exc).__name__
                except Exception as exc:
                    self.breaker.record(adapter.name, False)
                    return [], SupportStatus.UNAVAILABLE, type(exc).__name__

        for adapter in self.adapters:
            for lang in ("es", "en", "zh", "ru"):
                term = expansion.terms.get(lang)
                if term and lang in adapter.accepts_text_languages:
                    task = asyncio.create_task(one(adapter, lang, term))
                    tasks[task] = (adapter.name, lang, term)
        finished, pending = (
            await asyncio.wait(
                tasks,
                timeout=self.discovery_seconds,
            )
            if tasks
            else (set(), set())
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        executions = []
        seen = {
            f"{clip['source']['provider']}:{clip['source']['provider_id']}"
            for clip in catalog.clips
        }
        candidates = []
        duplicates = 0
        for task in tasks:
            provider, language, term = tasks[task]
            if task in pending:
                found, status, error = [], SupportStatus.TIMEOUT, "discovery_deadline"
            else:
                found, status, error = task.result()
            executions.append(
                Execution(
                    provider,
                    language,
                    term,
                    status,
                    len(found),
                    max(0.0, self.clock() - start),
                    error,
                )
            )
            for candidate in found:
                if candidate.origin_id in seen:
                    duplicates += 1
                    continue
                seen.add(candidate.origin_id)
                if len(candidates) < self.max_candidates:
                    candidates.append(candidate)
        return SearchResult(
            expansion, local, candidates, executions, self.clock() - start, duplicates
        )
