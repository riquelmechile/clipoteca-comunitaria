"""Regression guards for the M6 repair round (offline, no provider network)."""

import asyncio

import httpx
import pytest

from clipoteca.catalog import Catalog
from clipoteca.engine import SearchEngine
from clipoteca.jobs import JobStore, new_job, request_id
from clipoteca.metadata import MetadataTranslationError, humanize_source_title, localize_candidate
from clipoteca.providers.base import Adapter, Candidate, MediaRef, RateLimited, SearchLimits, SupportStatus, ProviderError
from clipoteca.providers.bilibili import Bilibili
from clipoteca.providers.http import fetch_json
from clipoteca.providers.vk_video import VKVideo


def test_humanized_source_and_argos_required_even_for_dictionary_term():
    assert humanize_source_title("rain-on-the-streets-27690364") == "Rain on the streets"
    candidate = Candidate("pexels", "27690364", "lluvia", "es", "https://pexels.com/video/27690364",
                          "rain-on-the-streets-27690364", "en", metadata={"description": "Wet road"})
    translated = localize_candidate(candidate, translator=lambda text, src, dst: f"ES: {text}" if dst == "es" else f"EN: {text}")
    assert translated.title == {"es": "ES: Rain on the streets", "en": "Rain on the streets"}
    assert translated.description == {"es": "ES: Wet road", "en": "Wet road"}
    assert translated.original_title == "rain-on-the-streets-27690364"
    assert "Rain" in translated.tags["en"]


def test_missing_argos_must_not_fallback_to_word_substitution(monkeypatch):
    monkeypatch.delenv("ARGOS_MODEL_PINS_JSON", raising=False)
    candidate = Candidate("pexels", "2", "lluvia", "es", "https://pexels.com/v/2", "rain", "en")
    with pytest.raises(MetadataTranslationError, match="translation_failed"):
        localize_candidate(candidate)


def test_terminal_jobs_retry_only_with_incrementing_attempt(tmp_path):
    rid = request_id("lluvia", {"max_duration": 20}, "v1")
    assert rid == request_id("lluvia", {"aspect_ratio": None, "max_duration": 20}, "v1")
    jobs = JobStore(tmp_path)
    job = new_job(rid)
    jobs.save(job)
    job["status"] = "rate_limited"
    jobs.save(job)
    with pytest.raises(ValueError, match="terminal"):
        jobs.save({**job, "status": "queued", "attempt": 1})
    renewed = new_job(rid)
    renewed["attempt"] = 2
    jobs.save(renewed)
    assert jobs.load(rid)["attempt"] == 2
    renewed["status"] = "published"
    jobs.save(renewed)
    with pytest.raises(ValueError, match="terminal"):
        jobs.save({**renewed, "status": "queued", "attempt": 3})


class Fast(Adapter):
    name = "fast"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    async def search(self, term, lang, limits):
        await asyncio.sleep(0.002)
        return [Candidate(self.name, "same", term, lang, "https://example.com", "Rain", "en")]

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return MediaRef("https://example.com/rain.mp4", "video/mp4")


class YoutubeSlow(Fast):
    name = "youtube"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY

    async def search(self, term, lang, limits):
        await asyncio.sleep(5)
        return []


@pytest.mark.asyncio
async def test_youtube_is_nonblocking_and_existing_duplicate_has_real_id():
    existing = Catalog([{"id": "clip-000099", "source": {"provider": "fast", "provider_id": "same"},
                         "title": {"es": "Lluvia", "en": "Rain"}, "description": {"es": "Lluvia", "en": "Rain"},
                         "tags": {"es": [], "en": []}, "media": {"sha256": "a" * 64}}])
    result = await SearchEngine([YoutubeSlow(), Fast()], discovery_seconds=0.2).search(
        "lluvia", catalog=existing
    )
    assert result.duplicate_ids == ["clip-000099"]
    assert result.duplicates == 4
    assert not result.candidates
    assert any(entry.provider == "youtube" for entry in result.executions)


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["header", "query"])
async def test_403_with_access_token_is_auth_not_generic_provider_error(credential):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"error": "forbidden"}))
    )
    kwargs = {"headers": {"access-token": "fake"}} if credential == "header" else {
        "params": {"access_token": "fake"}
    }
    from clipoteca.providers.base import AuthRequired
    with pytest.raises(AuthRequired):
        await fetch_json(client, "https://api.example.org/videos", **kwargs)


@pytest.mark.asyncio
async def test_bilibili_three_412_errors_become_ip_blocked():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(412)))
    source = Bilibili(client=client)
    for _ in range(2):
        with pytest.raises(RateLimited):
            await source.search("lluvia", "es", SearchLimits())
    with pytest.raises(ProviderError, match="ip_blocked"):
        await source.search("lluvia", "es", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [6, 9])
async def test_vk_api_flood_codes_rate_limited(code):
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"error": {"error_code": code}})
    ))
    with pytest.raises(RateLimited):
        await VKVideo(token="fake", client=client).search("rain", "en", SearchLimits())
