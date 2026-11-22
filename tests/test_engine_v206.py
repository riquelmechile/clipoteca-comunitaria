"""v2.0.6: provider-level fairness, own deadlines and credential preflight."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import Counter

import pytest

from clipoteca.engine import SearchEngine
from clipoteca.providers.base import Adapter, Candidate, MediaRef, SupportStatus


class Stub(Adapter):
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name, *, delay=0.0005, hang=False, status=None):
        self.name = name
        self.delay = delay
        self.hang = hang
        self.called = []
        self.active = 0
        self.peak = 0
        if status is not None:
            self.support_status = status

    async def search(self, term, lang, limits):
        self.called.append(lang)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.hang:
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(self.delay)
            return [
                Candidate(
                    self.name,
                    f"{lang}-1",
                    term,
                    lang,
                    f"https://example.org/{self.name}/{lang}",
                    "Rain",
                    "en",
                )
            ]
        finally:
            self.active -= 1

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return MediaRef("https://example.org/media.mp4", "video/mp4")


@pytest.mark.asyncio
async def test_twenty_three_providers_four_languages_all_reach_russian():
    providers = [Stub(f"p{i:02d}") for i in range(23)]
    providers[0].delay = 0.2
    # Own timeouts are small, so one slow provider fails without blocking others.
    ticks = itertools.count()
    started = time.monotonic()
    result = await SearchEngine(
        providers,
        concurrency=8,
        per_source_seconds=0.018,
        discovery_seconds=0.01,
        clock=lambda: next(ticks) / 1000,
    ).search("lluvia")
    elapsed = time.monotonic() - started
    assert elapsed < 0.6
    assert len(result.executions) == 92
    assert all(set(provider.called) == {"es", "en", "zh", "ru"} for provider in providers)
    assert all(provider.peak <= 1 for provider in providers)
    assert not any(
        row.error in {"discovery_deadline", "discovery_hard_cap"} for row in result.executions
    )
    assert all(
        row.error == "provider_timeout" for row in result.executions if row.provider == "p00"
    )
    assert sum(row.language == "ru" and row.found for row in result.executions) == 22
    assert len(result.candidates) <= 100
    assert len({candidate.provider for candidate in result.candidates}) == 22


@pytest.mark.asyncio
async def test_hung_forever_gets_own_timeout_and_total_cap():
    healthy = Stub("healthy")
    stuck = Stub("forever", hang=True)
    start = time.monotonic()
    result = await SearchEngine(
        [stuck, healthy],
        concurrency=1,
        per_source_seconds=0.015,
        discovery_seconds=0.001,
    ).search("lluvia")
    assert time.monotonic() - start < 0.35
    assert set(healthy.called) == {"es", "en", "zh", "ru"}
    assert set(stuck.called) == {"es", "en", "zh", "ru"}
    assert {row.error for row in result.executions if row.provider == "forever"} == {
        "provider_timeout"
    }
    assert all(row.error != "discovery_hard_cap" for row in result.executions)


@pytest.mark.asyncio
async def test_known_auth_required_never_uses_a_slot():
    missing = Stub("missing", status=SupportStatus.AUTH_REQUIRED)
    ready = Stub("ready")
    result = await SearchEngine(
        [missing, ready],
        concurrency=1,
        per_source_seconds=0.03,
        discovery_seconds=0.01,
    ).search("lluvia")
    assert missing.called == []
    assert set(ready.called) == {"es", "en", "zh", "ru"}
    assert Counter(row.error for row in result.executions if row.provider == "missing") == {
        "auth_required": 4
    }


@pytest.mark.asyncio
async def test_candidate_cap_interleaves_providers_before_truncating():
    trio = [Stub(name) for name in ("pexels", "pixabay", "wikimedia_commons")]
    result = await SearchEngine(trio, max_candidates=3).search("lluvia")
    assert len(result.candidates) == 3
    assert {row.provider for row in result.candidates} == {"pexels", "pixabay", "wikimedia_commons"}
