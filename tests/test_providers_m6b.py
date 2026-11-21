"""Official Douyin Open Platform and TikTok Display API contract fixtures."""

import httpx
import pytest

from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
)
from clipoteca.providers.douyin import Douyin
from clipoteca.providers.tiktok import TikTok


def dy_client(payload, status=200):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=payload, headers={"Retry-After": "0"})
        )
    )


def tk_client(payload, status=200):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_douyin_authorized_owner_only_four_search_terms(lang):
    def handler(request):
        assert request.url.path == "/video/list/"
        assert request.headers["access-token"] == "test"
        return httpx.Response(
            200,
            json={
                "data": {
                    "error_code": 0,
                    "list": [
                        {"item_id": "123456789101112", "title": "Rain in street"},
                        {"item_id": "123456789101113", "title": "A city"},
                    ],
                }
            },
        )

    adapter = Douyin(token="test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    found = await adapter.search("Rain", lang, SearchLimits())
    assert len(found) == 1 and found[0].source_language == "en"
    assert found[0].language == lang
    assert await adapter.direct_media(found[0]) is None
    assert found[0].metadata["scope"] == "authorized_account_only"


@pytest.mark.asyncio
async def test_douyin_missing_or_invalid_oauth():
    with pytest.raises(AuthRequired):
        await Douyin(token="").search("rain", "es", SearchLimits())
    with pytest.raises(AuthRequired):
        await Douyin(token="bad", client=dy_client({"data": {"error_code": 2100004}})).search(
            "rain", "zh", SearchLimits()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [(429, RateLimited), (500, ProviderError)])
async def test_douyin_errors(code, expected):
    adapter = Douyin(token="test", client=dy_client({}, code))
    with pytest.raises(expected):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_douyin_empty_and_timeout():
    assert (
        await Douyin(token="t", client=dy_client({"data": {"error_code": 0, "list": []}})).search(
            "rain", "en", SearchLimits()
        )
        == []
    )

    def timeout(_):
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        await Douyin(
            token="t", client=httpx.AsyncClient(transport=httpx.MockTransport(timeout))
        ).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_tiktok_display_list_is_account_only(lang):
    def handler(request):
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test"
        assert request.url.params["fields"].startswith("id,title")
        return httpx.Response(
            200,
            json={
                "data": {
                    "videos": [
                        {
                            "id": "7077642457847994444",
                            "title": "Rain in city",
                            "share_url": "https://www.tiktok.com/@demo/video/7077642457847994444",
                            "duration": 11,
                        },
                        {"id": "7077642457847994445", "title": "Sunny day"},
                    ],
                    "has_more": False,
                },
                "error": {"code": "ok", "message": ""},
            },
        )

    found = await TikTok(
        token="test", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ).search("Rain", lang, SearchLimits())
    assert len(found) == 1 and found[0].language == lang
    assert found[0].source_language == "en"
    assert found[0].duration_seconds == 11
    assert await TikTok(token="test").direct_media(found[0]) is None


@pytest.mark.asyncio
async def test_tiktok_missing_invalid_token_and_empty():
    with pytest.raises(AuthRequired):
        await TikTok(token="").search("rain", "en", SearchLimits())
    with pytest.raises(AuthRequired):
        await TikTok(
            token="no", client=tk_client({"error": {"code": "access_token_invalid"}}, 200)
        ).search("rain", "en", SearchLimits())
    assert (
        await TikTok(
            token="t", client=tk_client({"data": {"videos": []}, "error": {"code": "ok"}})
        ).search("rain", "zh", SearchLimits())
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exc", [(429, RateLimited), (500, ProviderError)])
async def test_tiktok_http_failures(code, exc):
    with pytest.raises(exc):
        await TikTok(token="test", client=tk_client({}, code)).search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_tiktok_timeout():
    def timeout(_):
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(ProviderTimeout):
        await TikTok(
            token="test", client=httpx.AsyncClient(transport=httpx.MockTransport(timeout))
        ).search("rain", "en", SearchLimits())
