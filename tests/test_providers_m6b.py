"""M6b regressions updated for the M19 public-web provider contract."""

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


def guest(request):
    if request.url.host == "ttwid.bytedance.com":
        return httpx.Response(200, json={}, headers={"Set-Cookie": "ttwid=guest-123; Path=/"})
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_douyin_public_web_search_in_four_languages(lang):
    def handler(request):
        init = guest(request)
        if init:
            return init
        assert request.url.path == "/aweme/v1/web/search/item/"
        assert request.url.params["keyword"] == "rain"
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "data": [
                    {"aweme_info": {"aweme_id": "7381000000000000001", "desc": "Rain street"}}
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Douyin(client=client).search("rain", lang, SearchLimits(max_pages=1))
    assert len(result) == 1 and result[0].language == lang
    assert result[0].metadata["scope"] == "public_search"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_tiktok_public_web_search_in_four_languages(lang):
    def handler(request):
        init = guest(request)
        if init:
            return init
        assert request.url.path == "/api/search/item/full/"
        assert request.url.params["X-Gnarly"]
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "item_list": [{"id": "7400000000000000001", "desc": "Rain street"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await TikTok(client=client).search("rain", lang, SearchLimits(max_pages=1))
    assert len(result) == 1 and result[0].language == lang
    assert result[0].metadata["scope"] == "public_search"


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [Douyin, TikTok])
async def test_missing_old_access_token_does_not_block_search(cls, monkeypatch):
    monkeypatch.delenv("DOUYIN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)

    def handler(request):
        init = guest(request)
        if init:
            return init
        return httpx.Response(200, json={"status_code": 0, "data": [], "item_list": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cls(client=client).search("rain", "en", SearchLimits(max_pages=1)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [Douyin, TikTok])
@pytest.mark.parametrize("http_status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_http_error_mapping(cls, http_status, exc):
    def handler(request):
        init = guest(request)
        if init:
            return init
        return httpx.Response(http_status, json={}, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(exc):
            await cls(client=client).search("rain", "en", SearchLimits(max_pages=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [Douyin, TikTok])
async def test_timeout_mapping(cls):
    def handler(request):
        if request.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={})
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderTimeout):
            await cls(client=client).search("rain", "en", SearchLimits(max_pages=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [Douyin, TikTok])
async def test_session_required_is_not_generic_provider_error(cls):
    def handler(request):
        init = guest(request)
        if init:
            return init
        return httpx.Response(200, json={"status_code": 2483})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthRequired, match="session_required"):
            await cls(client=client).search("rain", "en", SearchLimits(max_pages=1))
