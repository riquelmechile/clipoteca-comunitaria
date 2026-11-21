"""Official VK v5.199 and live Bilibili search payload contract fixtures."""

import httpx
import pytest

from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
)
from clipoteca.providers.bilibili import Bilibili
from clipoteca.providers.vk_video import VKVideo

_WBI_NAV = {
    "code": -101,
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_bilibili_search_real_shape(lang):
    def handler(req):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=_WBI_NAV)
        if req.url.path.endswith("/view"):
            return httpx.Response(200, json={"code": 0, "data": {}})
        assert req.url.params["search_type"] == "video"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "result": [
                        {
                            "bvid": "BV1xx411c7mD",
                            "title": "<em class='keyword'>Rain</em> videos",
                            "duration": "05:24",
                            "is_pay": False,
                            "description": "forest",
                        },
                        {"bvid": "broken", "title": "Fake"},
                    ],
                },
            },
        )

    adapter = Bilibili(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await adapter.search("lluvia", lang, SearchLimits())
    assert len(result) == 1
    assert result[0].source_title == "Rain videos"
    assert result[0].source_language == "en"
    assert result[0].language == lang
    assert await adapter.direct_media(result[0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,exc", [(-352, RateLimited), (-412, RateLimited), (500, ProviderError)]
)
async def test_bilibili_error_codes(code, exc):
    def handler(req):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=_WBI_NAV)
        return httpx.Response(200, json={"code": code})

    adapter = Bilibili(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(exc):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_bilibili_empty_and_network_timeout():
    def handler(req):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=_WBI_NAV)
        return httpx.Response(200, json={"code": 0, "data": {"result": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await Bilibili(client=client).search("rain", "en", SearchLimits()) == []

    def timeout(_):
        raise httpx.ReadTimeout("time")

    with pytest.raises(ProviderTimeout):
        await Bilibili(client=httpx.AsyncClient(transport=httpx.MockTransport(timeout))).search(
            "rain", "en", SearchLimits()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_vk_search_official_shape(lang):
    def handler(req):
        assert req.url.params["v"] == "5.199"
        assert req.url.params["access_token"] == "test"
        return httpx.Response(
            200,
            json={
                "response": {
                    "count": 1,
                    "items": [
                        {
                            "owner_id": -77,
                            "id": 42,
                            "title": "Rain in the city",
                            "duration": 32,
                            "files": {"mp4_360": "https://cs.vk.com/test.mp4"},
                        }
                    ],
                }
            },
        )

    adapter = VKVideo(
        token="test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    found = await adapter.search("\u0434\u043e\u0436\u0434\u044c", lang, SearchLimits())
    assert found[0].source_url == "https://vk.com/video-77_42"
    assert found[0].source_language == "en"
    assert found[0].language == lang
    media = await adapter.direct_media(found[0])
    assert media and media.mime == "video/mp4"


@pytest.mark.asyncio
async def test_vk_invalid_configured_token():
    adapter = VKVideo(
        token="bad",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"error": {"error_code": 5}})
            )
        ),
    )
    with pytest.raises(AuthRequired):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_vk_http_status(http_status, exc):
    adapter = VKVideo(
        token="test",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    http_status, json={"error": "error"}, headers={"Retry-After": "0"}
                )
            )
        ),
    )
    with pytest.raises(exc):
        await adapter.search("rain", "ru", SearchLimits())


@pytest.mark.asyncio
async def test_vk_empty_search():
    adapter = VKVideo(
        token="test",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"response": {"count": 0, "items": []}})
            )
        ),
    )
    assert await adapter.search("rain", "en", SearchLimits()) == []
