"""Odysee live Lighthouse + LBRY SDK proxy result shapes verified Nov 20 2026."""

import httpx
import pytest

from clipoteca.providers.base import ProviderError, ProviderTimeout, RateLimited, SearchLimits
from clipoteca.providers.odysee import Odysee


CLAIM = "06dddc155fe81074278f89b2c89e3d554630d09b"
URI = "lbry://Waifu-VS-Rain#" + CLAIM
MEDIA = "https://player.odycdn.com/v6/streams/" + CLAIM + "/abcd.mp4"


def client(*, empty=False, search_status=200, size=23000, stream=MEDIA):
    def serve(request):
        if request.url.host == "lighthouse.odysee.tv":
            if search_status != 200:
                return httpx.Response(search_status, json={"error": "denied"})
            return httpx.Response(
                200,
                json=[]
                if empty
                else [
                    {"claimId": CLAIM, "name": "Waifu-VS-Rain", "channel_claim_id": "4" * 40},
                ],
            )
        if request.url.params.get("m") == "claim_search":
            payload = request.read().decode()
            assert "claim_search" in payload
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "result": {
                        "items": [
                            {
                                "claim_id": CLAIM,
                                "name": "Waifu-VS-Rain",
                                "permanent_url": URI,
                                "value": {
                                    "title": "Rain",
                                    "description": "Rain over a city",
                                    "source": {"size": str(size), "media_type": "video/mp4"},
                                    "video": {"duration": 12, "width": 640, "height": 360},
                                    "languages": ["en"],
                                },
                            }
                        ],
                    },
                    "id": 1,
                },
            )
        if request.url.params.get("m") == "resolve":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "result": {URI: {"name": "Waifu-VS-Rain"}}, "id": 1}
            )
        if request.url.params.get("m") == "get":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "result": {"streaming_url": stream}, "id": 1}
            )
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(serve))


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_odysee_search_four_languages_hydrates_claim_and_resolves_media(lang):
    adapter = Odysee(client=client())
    found = await adapter.search("lluvia", lang, SearchLimits())
    assert len(found) == 1
    c = found[0]
    assert c.provider_id == CLAIM and c.language == lang and c.source_language == "en"
    assert c.duration_seconds == 12
    assert c.source_url.startswith("https://odysee.com/")
    assert (await adapter.metadata(c))["size_bytes"] == 23000
    ref = await adapter.direct_media(c)
    assert ref and ref.url == MEDIA and ref.mime == "video/mp4"


@pytest.mark.asyncio
async def test_odysee_empty_is_empty_without_fake_media():
    adapter = Odysee(client=client(empty=True))
    assert await adapter.search("lluvia", "es", SearchLimits()) == []


@pytest.mark.asyncio
async def test_odysee_oversized_claim_refused_before_get():
    adapter = Odysee(client=client(size=2 * 1024 * 1024 * 1024))
    candidate = (await adapter.search("rain", "en", SearchLimits()))[0]
    assert await adapter.direct_media(candidate) is None


@pytest.mark.asyncio
async def test_odysee_hls_not_misreported_as_mp4():
    adapter = Odysee(client=client(stream="https://player.odycdn.com/video/list.m3u8"))
    candidate = (await adapter.search("rain", "en", SearchLimits()))[0]
    assert await adapter.direct_media(candidate) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_odysee_http_errors(status, exc):
    adapter = Odysee(client=client(search_status=status))
    with pytest.raises(exc):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_odysee_connection_timeout():
    def fail(_request):
        raise httpx.ReadTimeout("Odysee timed out")

    adapter = Odysee(client=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(ProviderTimeout):
        await adapter.search("rain", "en", SearchLimits())
