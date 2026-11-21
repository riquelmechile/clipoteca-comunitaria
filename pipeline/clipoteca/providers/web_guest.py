"""Shared guest/session flow for Douyin and TikTok public web discovery.

No credentials, signed URLs, or guest cookies enter Candidate metadata.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from .base import AuthRequired, ProviderError, ProviderTimeout, RateLimited
from .http import fetch_json, fetch_response
from .signing import BROWSER_UA, TTWID_REGISTER_URL


def safe_code(payload: dict) -> int:
    raw = payload.get("status_code", payload.get("statusCode", 0))
    if raw in (None, "", "0", 0):
        return 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise ProviderError("provider unexpected status code") from None


def require_success(payload: dict) -> None:
    code = safe_code(payload)
    if code == 2483:
        raise AuthRequired("session_required")
    if code in (401, 403):
        raise AuthRequired("provider session required")
    if code == 429:
        raise RateLimited("provider rate limit")
    if code:
        raise ProviderError(f"provider status {code}")


def media_url(value: object, roots: tuple[str, ...]) -> str | None:
    from .m17_common import allowed_https

    if not isinstance(value, str) or len(value) > 8192:
        return None
    link = value.replace("playwm", "play").replace("\\/", "/")
    if not allowed_https(link, roots):
        return None
    if urlsplit(link).username is not None:
        return None
    return link


def urls_in(value: object) -> list[str]:
    if isinstance(value, dict):
        return urls_in(value.get("url_list") or value.get("urlList") or value.get("UrlList"))
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    if isinstance(value, str):
        return [value]
    return []


class GuestSession:
    """One in-process identity, never persisted; secret cookie is fallback-only."""

    def __init__(self, client: httpx.AsyncClient, platform: str, *, session_cookie: str = ""):
        self.client = client
        self.platform = platform
        self.session_cookie = session_cookie.strip()
        self.ttwid = ""
        self._initialized = False
        self.used_secret = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        aid = 6383 if self.platform == "douyin" else 1988
        body = json.dumps(
            {"region": "cn", "aid": aid, "service": f"www.{self.platform}.com", "needFid": False}
        )
        try:
            result = await fetch_response(
                self.client,
                TTWID_REGISTER_URL,
                method="POST",
                data=body,
                headers={"Content-Type": "text/plain", "User-Agent": BROWSER_UA},
            )
            if result.status_code == 429:
                raise RateLimited("guest identity rate limited")
            if result.status_code < 400:
                self.ttwid = result.cookies.get("ttwid") or self.client.cookies.get("ttwid", "")
        except (ProviderError, ProviderTimeout):
            # A failed optional registration must not force a secret-cookie attempt.
            self.ttwid = ""

    def headers(self, *, use_secret: bool = False) -> dict[str, str]:
        out = {"User-Agent": BROWSER_UA, "Referer": f"https://www.{self.platform}.com/"}
        if (use_secret or self.used_secret) and self.session_cookie:
            out["Cookie"] = self.session_cookie
        elif self.ttwid:
            out["Cookie"] = f"ttwid={self.ttwid}"
        return out

    def cookie_value(self, name: str) -> str:
        # Until an explicit 2483 fallback, a configured secret must be ignored.
        if self.used_secret:
            for pair in self.session_cookie.split(";"):
                key, _, value = pair.strip().partition("=")
                if key == name and value:
                    return value
        try:
            return self.client.cookies.get(name) or ""
        except httpx.CookieConflict:
            return ""

    async def json(
        self,
        url: str,
        params: dict[str, str],
        *,
        secret_url_factory: Callable[[], Awaitable[str]] | None = None,
    ) -> dict:
        await self.initialize()
        try:
            answer = await fetch_json(
                self.client, url, params=params or None, headers=self.headers()
            )
            require_success(answer)
            return answer
        except AuthRequired:
            if not self.session_cookie or self.used_secret:
                raise
            # TikTok signatures include msToken. Retry using the secret identity.
            self.used_secret = True
            try:
                retry_url = await secret_url_factory() if secret_url_factory else url
                answer = await fetch_json(
                    self.client,
                    retry_url,
                    params=params or None,
                    headers=self.headers(use_secret=True),
                )
                require_success(answer)
                return answer
            except (AuthRequired, ProviderError, ProviderTimeout):
                self.used_secret = False
                raise
