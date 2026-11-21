"""Offline official-shape fixtures for M18 institutional archives."""

import json
from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, Candidate, SearchLimits, SupportStatus
from clipoteca.providers.dpla import DPLA
from clipoteca.providers.europeana import Europeana
from clipoteca.providers.nara import NARA
from clipoteca.providers.smithsonian import Smithsonian

FIXTURES = Path(__file__).parent / "fixtures" / "m18"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def adapter_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [Europeana, NARA, DPLA, Smithsonian])
async def test_archive_adapters_require_key_before_network(adapter_type):
    adapter = adapter_type(
        api_key="",
        client=adapter_client(lambda _: pytest.fail("missing key must not make a request")),
    )
    with pytest.raises(AuthRequired):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_europeana_video_search_cursor_and_direct_media():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["X-Api-Key"] == "fake"
        assert request.url.params["qf"] == "TYPE:VIDEO"
        assert request.url.params["media"] == "true"
        if request.url.params["cursor"] == "*":
            return httpx.Response(200, json=fixture("europeana.json"))
        return httpx.Response(200, json={"items": []})

    adapter = Europeana(api_key="fake", client=adapter_client(handler))
    found = await adapter.search("lluvia", "es", SearchLimits(max_pages=2))
    assert len(found) == 1 and len(requests) == 2
    assert found[0].origin_id.startswith("europeana:")
    assert found[0].source_language == "es"
    media = await adapter.direct_media(found[0])
    assert media and media.url == "https://media.example.org/rain.mp4"


@pytest.mark.asyncio
async def test_nara_search_after_header_and_direct_media():
    def handler(request):
        assert request.headers["x-api-key"] == "fake"
        assert request.url.params["searchAfter"] == "*"
        assert request.url.params["limit"] == "20"
        return httpx.Response(200, json=fixture("nara.json"))

    adapter = NARA(api_key="fake", client=adapter_client(handler))
    found = await adapter.search("rain", "en", SearchLimits(max_pages=2))
    assert [row.provider_id for row in found] == ["12345"]
    media = await adapter.direct_media(found[0])
    assert media and media.mime == "video/webm" and media.size_bytes == 2048


@pytest.mark.asyncio
async def test_dpla_filters_moving_images_and_is_truthfully_search_only():
    def handler(request):
        assert request.url.params["api_key"] == "fake"
        assert request.url.params["sourceResource.type"] == "Moving Image"
        return httpx.Response(200, json=fixture("dpla.json"))

    adapter = DPLA(api_key="fake", client=adapter_client(handler))
    found = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert adapter.support_status == SupportStatus.SUPPORTED_SEARCH_ONLY
    assert [row.provider_id for row in found] == ["dpla-1"]
    assert await adapter.direct_media(found[0]) is None


@pytest.mark.asyncio
async def test_smithsonian_filters_video_but_does_not_promote_player_url():
    def handler(request):
        assert request.url.params["api_key"] == "fake"
        assert "online_media_type:Videos" in request.url.params["q"]
        return httpx.Response(200, json=fixture("smithsonian.json"))

    adapter = Smithsonian(api_key="fake", client=adapter_client(handler))
    found = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert adapter.support_status == SupportStatus.SUPPORTED_SEARCH_ONLY
    assert [row.provider_id for row in found] == ["edanmdm-nmah_123"]
    assert await adapter.direct_media(found[0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [DPLA, Smithsonian])
async def test_search_only_archives_return_explicit_direct_video_when_response_has_one(
    adapter_type,
):
    adapter = adapter_type(
        api_key="fake", client=adapter_client(lambda _: pytest.fail("no request"))
    )
    candidate = Candidate(
        provider=adapter.name,
        provider_id="direct-1",
        term="rain",
        language="en",
        source_url="https://example.org/item/direct-1",
        source_title="Direct video",
        source_language="en",
        metadata={
            "direct_video_url": "https://media.example.org/direct-1.mp4",
            "direct_video_mime": "video/mp4",
        },
    )
    media = await adapter.direct_media(candidate)
    assert media and media.url.endswith("direct-1.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_type",
    [Europeana, NARA, DPLA, Smithsonian],
)
async def test_empty_fixture_never_invents_archive_candidates(adapter_type):
    adapter = adapter_type(
        api_key="fake",
        client=adapter_client(lambda _: httpx.Response(200, json=fixture("empty.json"))),
    )
    assert await adapter.search("rain", "en", SearchLimits(max_pages=1)) == []
