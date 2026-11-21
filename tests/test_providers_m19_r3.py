"""M19 r3: challenges classified structurally, not via video title text."""

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, SearchLimits
from clipoteca.providers.douyin import Douyin
from clipoteca.providers.tiktok import TikTok


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
@pytest.mark.parametrize("term", ["captcha", "verify_check"])
async def test_legitimate_video_title_never_triggers_cookie_fallback(platform, term):
    sent: list[str | None] = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={}, headers={"set-cookie": "ttwid=guest; Path=/"})
        sent.append(req.headers.get("cookie"))
        item = (
            {"aweme_info": {"aweme_id": "7381000000000000001", "desc": f"rain {term} scene"}}
            if platform == "douyin"
            else {"id": "7400000000000000001", "desc": f"rain {term} scene"}
        )
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "data": [item] if platform == "douyin" else [],
                "item_list": [item] if platform == "tiktok" else [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie="session=TEST_NOT_USED", client=client)
            if platform == "douyin"
            else TikTok(cookie="session=TEST_NOT_USED", client=client)
        )
        results = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert len(results) == 1
    assert term in results[0].source_title
    assert sent == ["ttwid=guest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["douyin", "tiktok"])
@pytest.mark.parametrize("with_cookie", [False, True])
async def test_status_only_body_must_require_session(platform, with_cookie):
    sent: list[str | None] = []

    def handler(req):
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json={}, headers={"set-cookie": "ttwid=guest; Path=/"})
        sent.append(req.headers.get("cookie"))
        return httpx.Response(200, json={"status_code": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = (
            Douyin(cookie="session=TEST" if with_cookie else "", client=client)
            if platform == "douyin"
            else TikTok(cookie="session=TEST" if with_cookie else "", client=client)
        )
        with pytest.raises(AuthRequired, match="session_required"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert sent == (["ttwid=guest", "session=TEST"] if with_cookie else ["ttwid=guest"])
