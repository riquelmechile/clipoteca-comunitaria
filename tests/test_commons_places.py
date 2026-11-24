"""Mocked MediaWiki geographic discovery, with no network or studio extras."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import SearchLimits, SupportStatus
from clipoteca.providers.wikimedia_commons import MAX_CATEGORIES, MAX_MEMBERS, WikimediaCommons
from clipoteca.studio import library, scene_search

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "commons_place.json").read_text())


@pytest.mark.asyncio
async def test_geographic_results_first_and_alias_evidence():
    calls = []

    def handler(request):
        params = request.url.params
        calls.append(params)
        assert request.headers["user-agent"]
        assert params["maxlag"] == "5"
        if params.get("list") == "search":
            assert params["srnamespace"] == "14"
            assert "easter island" in params["srsearch"]
            return httpx.Response(200, json=_FIXTURE["categories"])
        if params.get("list") == "categorymembers":
            if params["cmtitle"] == "Category:Easter Island":
                return httpx.Response(200, json=_FIXTURE["subcategories"])
            return httpx.Response(200, json={"query": {"categorymembers": []}})
        if params.get("generator") == "categorymembers":
            assert params["gcmtype"] == "file"
            assert int(params["gcmlimit"]) <= MAX_MEMBERS
            if params["gcmtitle"] == "Category:Videos of Easter Island":
                return httpx.Response(200, json=_FIXTURE["files"])
            return httpx.Response(200, json={"query": {"pages": {}}})
        assert params["generator"] == "search"
        return httpx.Response(200, json=_FIXTURE["text"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = WikimediaCommons(client=client)
        found = await adapter.search("Rapa Nui moai", "es", SearchLimits())
        assert [candidate.provider_id for candidate in found] == ["1", "4"]
        assert found[0].metadata["commons_category"] == "Videos of Easter Island"
        assert found[0].source_title == "Coastal footage.webm"
        assert "commons_category" not in found[1].metadata
        assert (await adapter.direct_media(found[0])).mime == "video/webm"
        assert (await adapter.direct_media(found[1])).mime == "video/ogg"
        source = {
            "source_title": found[0].source_title,
            "commons_category": found[0].metadata["commons_category"],
            "commons_place_aliases": found[0].metadata["commons_place_aliases"],
        }
        record = {"title": {}, "source": source}
        for query in ("Rapa Nui moai", "Easter Island", "Isla de Pascua"):
            assert library.name_match(record, [query], source_only=True)
            assert library.sufficient_query_match(record, [query])
        assert not library.name_match(record, ["Perito Moreno"], source_only=True)
    categories = [call["gcmtitle"] for call in calls if call.get("generator") == "categorymembers"]
    assert categories.index("Category:Videos of Easter Island") < categories.index(
        "Category:Architecture of Easter Island"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "missing", "error"])
async def test_text_fallback(mode):
    calls = []

    def handler(request):
        params = request.url.params
        calls.append(params)
        if params.get("list") == "search":
            payload = (
                {"error": {"code": "maxlag"}} if mode == "error" else {"query": {"search": []}}
            )
            return httpx.Response(200, json=payload)
        assert params["generator"] == "search"
        return httpx.Response(200, json=_FIXTURE["text"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        term = "rain" if mode == "ordinary" else "Rapa Nui"
        found = await WikimediaCommons(client=client).search(term, "en", SearchLimits())
    assert [candidate.provider_id for candidate in found] == ["1", "4"]
    assert len(calls) == (1 if mode == "ordinary" else 2)


@pytest.mark.asyncio
async def test_category_depth_and_count_are_bounded():
    scanned = []

    def handler(request):
        params = request.url.params
        if params.get("list") == "search":
            return httpx.Response(200, json=_FIXTURE["categories"])
        if params.get("list") == "categorymembers":
            parent = params["cmtitle"]
            assert parent.count("/") < 2
            assert int(params["cmlimit"]) <= MAX_MEMBERS
            members = [{"ns": 14, "title": parent + "/" + str(i)} for i in range(10)]
            return httpx.Response(200, json={"query": {"categorymembers": members}})
        if params.get("generator") == "categorymembers":
            scanned.append(params["gcmtitle"])
            return httpx.Response(200, json={"query": {"pages": {}}})
        return httpx.Response(200, json=_FIXTURE["text"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await WikimediaCommons(client=client).search("Rapa Nui", "en", SearchLimits())
    assert len(scanned) == MAX_CATEGORIES
    assert max(title.count("/") for title in scanned) <= 2
    assert len(found) == 2


@pytest.mark.asyncio
async def test_original_name_hint_and_candidate_limit():
    def handler(request):
        params = request.url.params
        if params.get("list") == "search":
            assert "easter island" in params["srsearch"]
            return httpx.Response(200, json=_FIXTURE["categories"])
        if params.get("list") == "categorymembers":
            return httpx.Response(200, json={"query": {"categorymembers": []}})
        assert params["generator"] == "categorymembers"
        return httpx.Response(200, json=_FIXTURE["files"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        limits = SearchLimits(max_candidates=1, place_names=("rapa nui",))
        found = await WikimediaCommons(client=client).search("moai", "en", limits)
    assert [candidate.provider_id for candidate in found] == ["1"]


def test_priority_preserves_other_sources():
    class Stock:
        name = "pexels"
        discovery_priority = 10
        support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    stock = Stock()
    commons = WikimediaCommons()
    assert scene_search._ordered([stock, commons]) == [stock, commons]
    assert scene_search._ordered([stock, commons], place=True) == [commons, stock]
