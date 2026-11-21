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
