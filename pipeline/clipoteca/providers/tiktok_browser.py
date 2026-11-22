"""Optional, bounded TikTok-only browser discovery. Cookies remain in memory."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote, urlsplit

from .base import AuthRequired, ProviderError

# Browser identity measured in a successful GitHub Actions session probe.
_TIKTOK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RESPONSE = 8 * 1024 * 1024


def _browser_cookies(raw: str) -> list[dict[str, object]]:
    """Build bounded, TikTok-domain-only cookies from an HTTP Cookie header."""
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:].strip()
    if not raw or len(raw) > 16384 or any(c in raw for c in "\r\n\0"):
        return []
    result: list[dict[str, object]] = []
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            continue
        if not value or len(value) > 4096:
            continue
        result.append(
            {
                "name": name,
                "value": value,
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True,
                "sameSite": "None",
            }
        )
    return result


async def search_with_browser(
    term: str,
    cookie: str,
    max_pages: int,
    *,
    diagnostics: dict | None = None,
) -> list[dict]:
    """Capture valid /api/search/ responses; never export credentials or URLs."""
    cookies = _browser_cookies(cookie)
    if not cookies:
        raise AuthRequired("session_required")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ProviderError("tiktok_browser_unavailable") from None

    payloads: list[dict] = []
    pending: list[asyncio.Task] = []
    ready = asyncio.Event()
    counts = {
        "api_hits": 0,
        "valid_payloads": 0,
        "items": 0,
        "http_auth": 0,
        "oversize": 0,
        "largest_body_bytes": 0,
    }

    async def capture(response) -> None:
        try:
            url = urlsplit(response.url)
            if url.hostname != "www.tiktok.com" or not url.path.startswith("/api/search/"):
                return
            counts["api_hits"] += 1
            if response.status in (401, 403):
                counts["http_auth"] += 1
                return
            if response.status != 200:
                return
            body = await response.body()
            counts["largest_body_bytes"] = max(counts["largest_body_bytes"], len(body))
            if len(body) > _MAX_RESPONSE:
                counts["oversize"] += 1
                return
            if not body:
                return
            data = json.loads(body)
            if isinstance(data, dict):
                counts["valid_payloads"] += 1
                if len(payloads) < max(3, max_pages * 4):
                    payloads.append(data)
                from .tiktok import _items

                rows = _items(data)
                counts["items"] += len(rows)
                if rows:
                    ready.set()
        except Exception:
            # No upstream exception details, URLs, headers, cookies or raw JSON.
            return

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            try:
                context = await browser.new_context(
                    user_agent=_TIKTOK_UA,
                    locale="es-CL",
                    viewport={"width": 1366, "height": 900},
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
                        timeout=60000,
                    )
                except Exception:
                    # Sometimes the API succeeds while the page navigation times out.
                    pass
                for page_index in range(max(1, min(max_pages, 4))):
                    if page_index:
                        await page.mouse.wheel(0, 3000)
                    try:
                        await asyncio.wait_for(ready.wait(), timeout=12)
                    except TimeoutError:
                        pass
                    if ready.is_set():
                        break
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
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
    finally:
        if diagnostics is not None:
            diagnostics.update(counts)
    if not payloads:
        raise AuthRequired("session_required")
    return payloads
