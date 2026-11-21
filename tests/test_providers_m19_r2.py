"""M19 r2 guest identity blockers and exact one-shot fallback (offline)."""

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, SearchLimits
from clipoteca.providers.douyin import Douyin
from clipoteca.providers.tiktok import TikTok


def challenge(kind: str) -> httpx.Response:
    if kind == "empty_json":
        return httpx.Response(200, json={})
    if kind == "empty_body":
        return httpx.Response(200, content=b"", headers={"Content-Type": "application/json"})
    if kind == "non_json":
        return httpx.Response(200, text="<html>login</html>")
    if kind == "captcha":
        return httpx.Response(200, text="<html>captcha required</html>")
    if kind == "http_401":
        return httpx.Response(401, json={"message": "login"})
    if kind == "http_403":
        return httpx.Response(403, json={"message": "denied"})
    return httpx.Response(
        200,
        json={"status_code": 0, "search_nil_info": {"search_nil_type": "verify_check"}, "data": []},
    )


def accepted(platform: str) -> httpx.Response:
    if platform == "douyin":
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "data": [{"aweme_info": {"aweme_id": "7381000000000000001", "desc": "rain"}}],
            },
        )
    return httpx.Response(
        200, json={"status_code": 0, "item_list": [{"id": "7400000000000000001", "desc": "rain"}]}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
@pytest.mark.parametrize(
    "reason",
    ["empty_json", "empty_body", "non_json", "captcha", "http_401", "http_403", "verify_check"],
)
@pytest.mark.parametrize("cookie_enabled", [False, True])
async def test_guest_block_requires_session_or_single_cookie_fallback(
    platform: str, reason: str, cookie_enabled: bool
):
    cookie = "session=EXAMPLE_TEST_ONLY"
    sent = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={}, headers={"Set-Cookie": "ttwid=guest; Path=/"})
        sent.append(req.headers.get("cookie"))
        if len(sent) == 1:
            return challenge(reason)
        assert len(sent) == 2 and sent[1] == cookie
        return accepted(platform)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie=cookie if cookie_enabled else "", client=client)
            if platform == "douyin"
            else TikTok(cookie=cookie if cookie_enabled else "", client=client)
        )
        if cookie_enabled:
            rows = await adapter.search("rain", "en", SearchLimits(max_pages=1))
            assert len(rows) == 1
            assert sent == ["ttwid=guest", cookie]
            assert cookie not in repr(rows)
        else:
            with pytest.raises(AuthRequired, match="session_required"):
                await adapter.search("rain", "en", SearchLimits(max_pages=1))
            assert sent == ["ttwid=guest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_failed_cookie_retry_is_session_required_once(platform):
    seen = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={}, headers={"Set-Cookie": "ttwid=guest; Path=/"})
        seen.append(req.headers.get("cookie"))
        return challenge("http_403")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie="session=example", client=client)
            if platform == "douyin"
            else TikTok(cookie="session=example", client=client)
        )
        with pytest.raises(AuthRequired, match="session_required"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert seen == ["ttwid=guest", "session=example"]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
async def test_explicit_empty_search_is_not_a_block(platform):
    sent = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={}, headers={"Set-Cookie": "ttwid=guest; Path=/"})
        sent.append((req.url.path, req.headers.get("cookie")))
        return httpx.Response(200, json={"status_code": 0, "data": [], "item_list": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie="session=example", client=client)
            if platform == "douyin"
            else TikTok(cookie="session=example", client=client)
        )
        assert await adapter.search("rain", "en", SearchLimits(max_pages=1)) == []
    assert len(sent) == (2 if platform == "douyin" else 1)
    assert all(cookie == "ttwid=guest" for _path, cookie in sent)
