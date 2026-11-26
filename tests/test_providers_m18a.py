from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from clipoteca.providers.base import AuthRequired, SearchLimits, SupportStatus
from clipoteca.providers.coverr import Coverr
from clipoteca.providers.library_of_congress import LibraryOfCongress
from clipoteca.sources import list_sources
from fake_secrets import fake_secret

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(provider: str, name: str) -> dict:
    return json.loads((FIXTURES / provider / name).read_text(encoding="utf-8"))


def http_client(body: dict, requests: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json=body, headers={"content-type": "application/json"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_library_of_congress_filters_restricted_and_resolves_video_fields():
    requests: list[httpx.Request] = []
    adapter = LibraryOfCongress(
        client=http_client(fixture("library_of_congress", "search.json"), requests)
    )
    found = await adapter.search("lluvia", "es", SearchLimits(max_pages=1))

    assert [item.provider_id for item in found] == ["2021667581", "2022888123"]
    assert [item.origin_id for item in found] == [
        "library_of_congress:2021667581",
        "library_of_congress:2022888123",
    ]
    assert (await adapter.direct_media(found[0])).mime == "video/mp4"
    assert (await adapter.direct_media(found[1])).mime == "video/webm"
    assert requests[0].url.params["fo"] == "json"
    assert requests[0].url.params["at"] == "results,pagination"
    assert requests[0].url.params["q"] == "lluvia"


@pytest.mark.asyncio
async def test_library_of_congress_empty_fixture():
    adapter = LibraryOfCongress(client=http_client(fixture("library_of_congress", "empty.json")))
    assert await adapter.search("rain", "en", SearchLimits()) == []


@pytest.mark.asyncio
async def test_coverr_uses_only_download_and_rejects_preview_only_rows():
    requests: list[httpx.Request] = []
    api_key = fake_secret("coverr")
    adapter = Coverr(
        api_key=api_key, client=http_client(fixture("coverr", "search.json"), requests)
    )
    found = await adapter.search("雨", "zh", SearchLimits(max_pages=1))

    assert [item.provider_id for item in found] == ["coverr-rain-1"]
    assert (await adapter.direct_media(found[0])).url.endswith("rain-on-glass.mp4")
    assert requests[0].headers["Authorization"] == "Bearer " + api_key
    assert requests[0].url.params["urls"] == "true"


@pytest.mark.asyncio
async def test_coverr_no_key_and_empty_fixture():
    missing = Coverr(api_key="", client=http_client({}))
    with pytest.raises(AuthRequired, match="COVERR_API_KEY"):
        await missing.search("rain", "en", SearchLimits())

    adapter = Coverr(
        api_key=fake_secret("coverr-empty"), client=http_client(fixture("coverr", "empty.json"))
    )
    assert await adapter.search("rain", "en", SearchLimits()) == []


def test_new_sources_are_registered_without_exposing_values():
    value = fake_secret("coverr-env")
    rows = {row["name"]: row for row in list_sources(env={"COVERR_API_KEY": value})}

    assert rows["library_of_congress"]["status"] == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    assert rows["library_of_congress"]["requires"] == []
    assert rows["coverr"]["status"] == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    assert rows["coverr"]["requires"] == ["COVERR_API_KEY"]
    assert value not in json.dumps(rows)


def test_coverr_source_reports_missing_credential_name_only():
    row = next(item for item in list_sources(env={}) if item["name"] == "coverr")
    assert row["status"] == SupportStatus.AUTH_REQUIRED
    assert row["missing_credential_names"] == ["COVERR_API_KEY"]
