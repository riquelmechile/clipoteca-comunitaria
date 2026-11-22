"""TikTok v2.0.5 session fallback and sanitized browser-search regressions."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, ProviderError, SearchLimits
from clipoteca.providers.tiktok import TikTok, _items
from clipoteca.providers.tiktok_browser import _browser_cookies


def _video(index: int) -> dict:
    identifier = f"74919590057456468{index:02d}"
    return {
        "item": {
            "id": identifier,
            "desc": "Aurora boreal northern lights",
            "author": {"uniqueId": "clipoteca38"},
            "video": {"playAddr": "https://v16-webapp-prime.tiktok.com/video.mp4"},
        }
    }


def test_browser_api_data_list_and_nested_items_parser():
    assert len(_items({"status_code": 0, "data": [_video(i) for i in range(4)]})) == 4
    assert len(_items({"status_code": 0, "item_list": [_video(0)]})) == 1
    assert _items({"status_code": 0, "data": None}) == []


def test_cookie_header_is_parsed_only_for_tiktok_domain():
    raw = "Cookie: sessionid=NOT_A_REAL_SECRET; sid_tt=OTHER; tt_chain_token=DUMMY"
    cookies = _browser_cookies(raw)
    assert {row["name"] for row in cookies} == {"sessionid", "sid_tt", "tt_chain_token"}
    assert all(row["domain"] == ".tiktok.com" for row in cookies)
    assert all(row["sameSite"] == "None" for row in cookies)
    assert _browser_cookies("sessionid=x\r\nInjected: value") == []


@pytest.mark.asyncio
async def test_browser_fallback_reuses_parser_and_limits(monkeypatch):
    from clipoteca.providers import tiktok_browser

    seen = []

    async def browser(term, cookie, max_pages):
        seen.append((term, max_pages))
        assert cookie == "sessionid=LOCAL_FAKE_COOKIE"
        return [{"status_code": 0, "data": [_video(i) for i in range(12)]}]

    monkeypatch.setattr(tiktok_browser, "search_with_browser", browser)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    ) as client:
        adapter = TikTok(cookie="sessionid=LOCAL_FAKE_COOKIE", client=client)
        monkeypatch.setattr(
            adapter,
            "_native_search",
            AsyncMock(side_effect=AuthRequired("session_required")),
        )
        candidates = await adapter.search(
            "aurora boreal", "es", SearchLimits(max_pages=2, max_candidates=3)
        )
        ref = await adapter.direct_media(candidates[0])
    assert len(candidates) == 3
    assert seen == [("aurora boreal", 2)]
    assert ref is not None
    assert ref.url.startswith("https://v16-webapp-prime.tiktok.com/")
    assert ref.allowed_hosts
    assert "LOCAL_FAKE_COOKIE" not in repr(candidates)


@pytest.mark.asyncio
async def test_expired_browser_session_is_auth_required_and_secret_redacted(monkeypatch):
    from clipoteca.providers import tiktok_browser

    async def expired(*_args):
        return [{"status_code": 2483, "data": []}]

    monkeypatch.setattr(tiktok_browser, "search_with_browser", expired)
    adapter = TikTok(cookie="sessionid=LOCAL_SECRET")
    monkeypatch.setattr(
        adapter,
        "_native_search",
        AsyncMock(side_effect=AuthRequired("session_required")),
    )
    with pytest.raises(AuthRequired, match="session_required") as err:
        await adapter.search("aurora boreal", "es", SearchLimits(max_pages=1))
    assert "LOCAL_SECRET" not in str(err.value)
    await adapter.client.aclose()


@pytest.mark.asyncio
async def test_absent_cookie_never_launches_browser(monkeypatch):
    from clipoteca.providers import tiktok_browser

    async def forbidden(*_args):
        pytest.fail("Browser must not run without a session cookie")

    monkeypatch.setattr(tiktok_browser, "search_with_browser", forbidden)
    adapter = TikTok(cookie="")
    monkeypatch.setattr(
        adapter,
        "_native_search",
        AsyncMock(side_effect=AuthRequired("session_required")),
    )
    with pytest.raises(AuthRequired):
        await adapter.search("aurora boreal", "es", SearchLimits(max_pages=1))
    await adapter.client.aclose()


@pytest.mark.asyncio
async def test_browser_internal_exception_never_exposes_cookie(monkeypatch):
    from clipoteca.providers import tiktok_browser

    class BrokenBrowser:
        async def __aenter__(self):
            raise RuntimeError("LOCAL_SECRET_SHOULD_NEVER_APPEAR")

        async def __aexit__(self, *_):
            return False

    import sys
    from types import ModuleType

    module = ModuleType("playwright.async_api")
    module.async_playwright = lambda: BrokenBrowser()
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    with pytest.raises(ProviderError) as exc:
        await tiktok_browser.search_with_browser(
            "aurora boreal", "sessionid=LOCAL_SECRET_SHOULD_NEVER_APPEAR", 1
        )
    assert "LOCAL_SECRET_SHOULD_NEVER_APPEAR" not in str(exc.value)
