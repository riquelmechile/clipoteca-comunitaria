"""Optional, bounded TikTok-only browser discovery. Cookies never leave memory."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, urlsplit

from .base import AuthRequired, ProviderError

# Known working search identity from the nonpersistent GitHub Actions probe.
_TIKTOK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RESPONSE = 2 * 1024 * 1024


def _browser_cookies(raw: str) -> list[dict[str, object]]:
    """Accept only bounded Cookie-header pairs scoped to .tiktok.com."""
    if not raw or len(raw) > 16384 or any(c in raw for c in "\r\n\0"):
        return []
    cookies: list[dict[str, object]] = []
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            continue
        if not value or len(value) > 4096:
            continue
        cookies.append(
            {"name": name, "value": value, "domain": ".tiktok.com", "path": "/", "secure": True}
        )
    return cookies


async def search_with_browser(term: str, cookie: str, max_pages: int) -> list[dict]:
    """Observe search API responses from a real browser; never serialize credentials."""
    cookies = _browser_cookies(cookie)
    if not cookies:
        raise AuthRequired("session_required")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ProviderError("tiktok_browser_unavailable") from None

    payloads: list[dict] = []
    pending: list[asyncio.Task] = []
    auth_http = False
    successful_responses = 0

    async def capture(response) -> None:
        nonlocal auth_http, successful_responses
        try:
            url = urlsplit(response.url)
            if url.hostname != "www.tiktok.com" or not url.path.startswith("/api/search/"):
                return
            if response.status in (401, 403):
                auth_http = True
                return
            if response.status != 200:
                return
            body = await response.body()
            if not body or len(body) > _MAX_RESPONSE:
                return
            data = json.loads(body)
            if isinstance(data, dict):
                successful_responses += 1
                if len(payloads) < max(3, max_pages * 4):
                    payloads.append(data)
        except Exception:
            # Browser errors and raw upstream text are not public diagnostics.
            return

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent=_TIKTOK_UA, locale="es-ES", viewport={"width": 1365, "height": 768}
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                page.on(
                    "response",
                    lambda response: pending.append(asyncio.create_task(capture(response))),
                )
                try:
                    await page.goto(
                        "https://www.tiktok.com/search/video?q=" + quote(term, safe=""),
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                except Exception:
                    # A late navigation failure may coexist with captured API results.
                    pass
                for page_index in range(max(1, min(max_pages, 4))):
                    if page_index:
                        await page.mouse.wheel(0, 4000)
                    await page.wait_for_timeout(5500)
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if any(
                        isinstance(payload.get("item_list") or payload.get("items"), list)
                        and (payload.get("item_list") or payload.get("items"))
                        for payload in payloads
                    ):
                        break
                login_modal = await page.locator(
                    '[data-e2e="login-modal"], [role="dialog"]:has-text("Log in")'
                ).count()
                if not payloads and (login_modal or "/login" in urlsplit(page.url).path):
                    raise AuthRequired("session_required")
                await context.close()
            finally:
                await browser.close()
    except AuthRequired:
        raise
    except Exception:
        raise ProviderError("tiktok_browser_unavailable") from None
    if not payloads or (auth_http and not successful_responses):
        raise AuthRequired("session_required")
    return payloads
