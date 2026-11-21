"""M15 fixtures: truthful IA search, ranking, derivative resolution and retries."""

from __future__ import annotations

import asyncio
from collections import Counter
import math
import time

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, SearchLimits
from clipoteca.providers.internet_archive import (
    BASE_QUERY,
    InternetArchive,
    build_query,
    normalize_terms,
    select_derivatives,
    _score,
)


def example_candidate(identifier: str = "documentary") -> Candidate:
    return Candidate(
        "internet_archive",
        identifier,
        "lluvia",
        "es",
        f"https://archive.org/details/{identifier}",
        "Rain documentary",
        "en",
    )


def test_normalize_and_lucene_query_preserves_four_languages():
    assert normalize_terms("The rain in the city and forest") == ["rain", "city", "forest"]
    assert normalize_terms("下雨 дождь lluvia rain") == ["下雨", "дождь", "lluvia", "rain"]
    terms = normalize_terms("rain: storm+")
    query = build_query(terms, all_terms=True)
    assert BASE_QUERY in query
    assert "title:" in query and "subject:" in query and "shotlist:" in query
    assert "title:\"rain\"" in query
    assert "title:\"storm\"" in query
    # Search terms containing literal Lucene operators must be escaped.
    assert '\\:' in build_query(["rain:storm"], all_terms=True)
    assert len(normalize_terms("one two three four five six seven eight")) <= 6


@pytest.mark.asyncio
async def test_advancedsearch_two_stages_and_relevance_order():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/advancedsearch.php"
        query = request.url.params["q"]
        calls.append(query)
        assert request.url.params.get_list("sort[]") == ["downloads desc", "identifier asc"]
        assert request.url.params["rows"] == "100"
        assert "h.264" in query
        assert request.url.params.get_list("fl[]")
        if " AND (title:" in query and " OR (title:" not in query:
            docs = [
                {
                    "identifier": "less_relevant",
                    "title": "Dry footage",
                    "description": "Rain and city",
                    "downloads": "100",
                }
            ]
        else:
            docs = [
                {
                    "identifier": "best",
                    "title": "Rain in city",
                    "subject": ["rain"],
                    "shotlist": "city street",
                    "downloads": 25,
                    "format": ["h.264"],
                }
            ]
        return httpx.Response(
            200,
            json={
                "responseHeader": {"params": {"q": query}},
                "response": {"docs": docs},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        found = await adapter.search("rain city", "en", SearchLimits(max_candidates=30))
    assert len(calls) == 2
    assert [row.provider_id for row in found] == ["best", "less_relevant"]
    assert found[0].term == "rain city"
    assert found[0].metadata["ranking_score"] > found[1].metadata["ranking_score"]


@pytest.mark.asyncio
async def test_query_echo_mismatch_is_rejected():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "responseHeader": {"params": {"q": "unrelated"}},
                "response": {"docs": [{"identifier": "wrong"}]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        with pytest.raises(ProviderError, match="query echo"):
            await adapter.search("rain", "en", SearchLimits())


def test_derivative_original_chain_prefers_small_playable():
    files = [
        {"name": "film-archive.mp4", "size": "9999999999", "format": "h.264"},
        {
            "name": "film-720p.mp4",
            "size": "50000000",
            "format": "h.264",
            "original": "film-archive.mp4",
            "source": "derivative",
        },
        {
            "name": "film-512kb.mp4",
            "size": "4500000",
            "format": "512Kb MPEG4",
            "original": "film-archive.mp4",
            "source": "derivative",
        },
        {"name": "film-notes.txt", "size": "4000"},
    ]
    chosen, scan = select_derivatives(files, "film_rain")
    assert chosen and chosen["name"] == "film-720p.mp4"
    assert scan and scan["name"] == "film-512kb.mp4"
    assert chosen["url"].startswith("https://archive.org/download/film_rain/")
    assert not select_derivatives([{"name": "../escape.mp4", "size": 1}], "a")[0]


@pytest.mark.asyncio
async def test_metadata_exposes_scanning_derivative_and_caches():
    count = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        count[request.url.path] += 1
        return httpx.Response(
            200,
            json={
                "metadata": {"identifier": "documentary"},
                "files": [
                    {
                        "name": "original.mov",
                        "size": "950000000",
                        "format": "QuickTime",
                    },
                    {
                        "name": "H264.mp4",
                        "original": "original.mov",
                        "size": "2500000",
                        "format": "h.264",
                    },
                    {
                        "name": "512Kb.mp4",
                        "original": "original.mov",
                        "size": "500000",
                        "format": "512Kb MPEG4",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        candidate = example_candidate()
        object.__setattr__(candidate, "metadata", {})
        item = await adapter.metadata(candidate)
        assert item["clipoteca_scan_media"]["name"] == "512Kb.mp4"
        assert candidate.metadata["scan_media"]["name"] == "512Kb.mp4"
        item["clipoteca_scan_media"]["name"] = "tampered.mp4"
        second = await adapter.metadata(candidate)
        assert second["clipoteca_scan_media"]["name"] == "512Kb.mp4"
        media = await adapter.direct_media(candidate)
    assert media and media.url.endswith("H264.mp4")
    assert count["/metadata/documentary"] == 1


@pytest.mark.asyncio
async def test_429_retry_after_and_503_then_200(monkeypatch):
    delays = []

    async def fake_sleep(seconds: float):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    statuses = [429, 503, 200]

    def handler(request: httpx.Request) -> httpx.Response:
        code = statuses.pop(0)
        if code == 200:
            return httpx.Response(200, json={
                "responseHeader": {"params": {"q": request.url.params["q"]}},
                "response": {"docs": []},
            })
        return httpx.Response(code, json={"detail": "rate limited"}, headers={"Retry-After": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        assert await adapter.search("rain", "en", SearchLimits()) == []
    assert len(statuses) == 0
    assert delays == [2.0, 2.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", [None, {}, {"params": {}}, {"params": {"q": "wrong"}}])
async def test_search_rejects_missing_or_mismatched_query_echo(echo):
    def handler(_request):
        response = {"response": {"docs": [{"identifier": "spoofed"}]}}
        if echo is not None:
            response["responseHeader"] = echo
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        with pytest.raises(ProviderError, match="query echo"):
            await adapter.search("rain", "en", SearchLimits())


def test_scan_stays_in_the_same_recording_original_chain():
    files = [
        {"name": "A-master.mov", "size": "1000000000"},
        {"name": "A-playable.mp4", "original": "A-master.mov",
         "size": "5000000", "format": "h.264"},
        {"name": "A-512kb.mp4", "original": "A-master.mov",
         "size": "800000", "format": "512Kb MPEG4"},
        {"name": "B-master.mov", "size": "1000000000"},
        {"name": "B-512kb.mp4", "original": "B-master.mov",
         "size": "10000", "format": "512Kb MPEG4"},
    ]
    playable, scan = select_derivatives(files, "multi_film")
    assert playable and playable["name"] == "A-playable.mp4"
    assert scan and scan["name"] == "A-512kb.mp4"


def test_archive_ranking_accepts_decimal_and_grouped_downloads():
    assert _score({"downloads": "12.5"}, []) == _score({"downloads": 12.5}, [])
    assert _score({"downloads": "1,234"}, []) == _score({"downloads": 1234}, [])
    assert _score({"downloads": "1,234.5"}, []) == _score({"downloads": 1234.5}, [])
    assert _score({"downloads": "1,234"}, []) > _score({"downloads": "12.5"}, [])
    assert math.isfinite(_score({"downloads": "NaN"}, []))


@pytest.mark.asyncio
async def test_item_without_video_is_search_only_and_not_caching_empty_files():
    requests = Counter()

    def handler(request):
        requests[request.url.path] += 1
        return httpx.Response(200, json={
            "metadata": {"identifier": "no_video"},
            "files": [{"name": "notes.txt", "size": "100"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        candidate = example_candidate("no_video")
        assert await adapter.direct_media(candidate) is None
        assert (await adapter.metadata(candidate))["clipoteca_media"] is None
    assert requests["/metadata/no_video"] == 1

    def empty_files(request):
        requests[request.url.path] += 1
        return httpx.Response(200, json={"metadata": {"identifier": "empty"}, "files": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(empty_files)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        empty = example_candidate("empty")
        assert (await adapter.metadata(empty))["clipoteca_media"] is None
        assert (await adapter.metadata(empty))["clipoteca_media"] is None
    assert requests["/metadata/empty"] == 2


@pytest.mark.asyncio
async def test_invalid_metadata_identifier_is_never_cached():
    requests = Counter()
    payloads = [
        {"metadata": {"identifier": "wrong"}, "files": []},
        {"files": [{"name": "video.mp4", "size": "2000"}]},
        {},
    ]

    def handler(request):
        requests[request.url.path] += 1
        return httpx.Response(200, json=payloads.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        for _ in range(3):
            with pytest.raises(ProviderError, match="identifier mismatch"):
                await adapter.metadata(example_candidate("wanted"))
    assert requests["/metadata/wanted"] == 3
    assert not adapter._metadata_cache


@pytest.mark.asyncio
async def test_cache_is_per_instance_limited_and_returns_deep_copies():
    requests = Counter()

    def handler(request):
        requests[request.url.path] += 1
        identifier = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "metadata": {"identifier": identifier, "subject": ["original"]},
            "files": [{"name": "film.mp4", "size": "1500", "format": "h.264"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        candidate = example_candidate("archive")
        first = await adapter.metadata(candidate)
        first["files"][0]["name"] = "mutated.mp4"
        first["metadata"]["subject"][0] = "tampered"
        again = await adapter.metadata(candidate)
        assert again["files"][0]["name"] == "film.mp4"
        assert again["metadata"]["subject"] == ["original"]
        other = InternetArchive(client=client, min_interval_seconds=0)
        await other.metadata(candidate)
    assert requests["/metadata/archive"] == 2
    assert len(adapter._metadata_cache) == 1


@pytest.mark.asyncio
async def test_host_rate_limit_shared_across_instances_and_direct_media():
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        identifier = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={
            "metadata": {"identifier": identifier},
            "files": [{"name": "film.mp4", "size": "2000", "format": "h.264"}],
        })

    with InternetArchive._slot_lock:
        InternetArchive._next_by_host.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = InternetArchive(client=client, min_interval_seconds=0.05)
        second = InternetArchive(client=client, min_interval_seconds=0.05)
        metadata, media = await asyncio.gather(
            first.metadata(example_candidate("first")),
            second.direct_media(example_candidate("second")),
        )
    assert metadata["metadata"]["identifier"] == "first"
    assert media and media.url.endswith("film.mp4")
    assert len(calls) == 2
    assert abs(calls[1] - calls[0]) >= 0.035


@pytest.mark.asyncio
async def test_other_providers_keep_default_only_429_retries_and_one_second_cap(monkeypatch):
    from clipoteca.providers.http import fetch_json

    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    attempts = [429, 429, 200]
    def handler(_req):
        code = attempts.pop(0)
        return httpx.Response(
            code, json={"ok": True}, headers={"Retry-After": "99"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await fetch_json(client, "https://example.org/other"))["ok"] is True
    assert sleeps == [1.0, 1.0]
    requests = []
    def unavailable(_req):
        requests.append(1)
        return httpx.Response(503, json={"detail": "unavailable"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as client:
        with pytest.raises(ProviderError, match="HTTP 503"):
            await fetch_json(client, "https://example.org/other")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_ia_503_retries_cap_retry_after_at_five_seconds(monkeypatch):
    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    attempts = [503, 503, 200]
    def handler(_req):
        status = attempts.pop(0)
        return httpx.Response(
            status,
            json={"metadata": {"identifier": "m15"}, "files": []},
            headers={"Retry-After": "500"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        assert (await adapter.metadata(example_candidate("m15")))["clipoteca_media"] is None
    assert not attempts
    assert sleeps == [5.0, 5.0]
