"""Fair, bounded discovery and strict four-language failure handling."""

import asyncio

import pytest

from clipoteca.engine import CircuitBreaker, SearchEngine
from clipoteca.providers.base import Adapter, Candidate, MediaRef, RateLimited, SupportStatus
from clipoteca.translate.expand import QueryExpansion


class Sample(Adapter):
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name, delay=0, failure=False):
        self.name = name
        self.delay = delay
        self.failure = failure
        self.active = 0
        self.max_active = 0
        self.received = []

    async def search(self, term, lang, limits):
        self.received.append(lang)
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            await asyncio.sleep(self.delay)
            if self.failure:
                raise RateLimited()
            return [Candidate(self.name, lang, term, lang, "https://example.org/x", "Rain", "en")]
        finally:
            self.active -= 1

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return MediaRef("https://example.org/video.mp4", "video/mp4")


@pytest.mark.asyncio
async def test_first_two_hung_sources_cannot_starve_last_healthy_source():
    a = Sample("hung-a", delay=10)
    b = Sample("hung-b", delay=10)
    good = Sample("healthy")
    result = await SearchEngine(
        [a, b, good], concurrency=4, discovery_seconds=0.16, per_source_seconds=0.3
    ).search("lluvia")
    assert good.received
    assert {entry.provider for entry in result.executions if entry.found} == {"healthy"}
    assert a.max_active == b.max_active == good.max_active == 1


@pytest.mark.asyncio
async def test_failed_translation_never_invokes_adapters():
    a = Sample("ready")
    expansion = QueryExpansion("unknown", {"es": "desconocido"}, errors={"en": "broken"})
    result = await SearchEngine([a]).search("desconocido", expansion=expansion)
    assert result.candidates == []
    assert result.executions == []
    assert a.received == []


@pytest.mark.asyncio
async def test_rate_limit_never_opens_breaker():
    breaker = CircuitBreaker(open_after=1)
    limited = Sample("limited", failure=True)
    await SearchEngine([limited], breaker=breaker).search("lluvia")
    assert not breaker.open("limited")
