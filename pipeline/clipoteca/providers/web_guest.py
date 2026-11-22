"""Shared guest/session flow for Douyin and TikTok public web discovery.

No credentials, signed URLs, or guest cookies enter Candidate metadata.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
import json

from .base import AuthRequired, ProviderError, ProviderTimeout, RateLimited
from .http import ProviderPayloadError, fetch_json, fetch_response
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
    """Classify actual guest verification fields, never user-generated titles.

    A nonempty status-only body is *not* a successful empty search; the
    provider must return an explicit result structure or detail record.
    """
    search_nil = payload.get("search_nil_info")
    if isinstance(search_nil, dict) and search_nil.get("search_nil_type") == "verify_check":
        raise AuthRequired("session_required")
    if any(
        key in payload and payload[key] not in (None, False, "", 0)
        for key in ("verify_data", "verify_center_decision_conf", "captcha")
    ):
        raise AuthRequired("session_required")
    code = safe_code(payload)
    if code in (2483, 401, 403):
        raise AuthRequired("session_required")
    if code == 429:
        raise RateLimited("provider rate limit")
    if code:
        raise ProviderError(f"provider status {code}")
    if not any(
        key in payload
        for key in (
            "data",
            "aweme_list",
            "item_list",
            "items",
            "aweme_detail",
            "itemInfo",
            "item_info",
        )
    ):
        raise AuthRequired("session_required")


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
        self._init_requires_session = False
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
                # Guest registration status 1001 was measured for TikTok
                # from Actions. Do not interpret a later empty result as
                # successful guest search when registration was rejected.
                if self.platform == "tiktok":
                    try:
                        registration = result.json()
                    except ValueError:
                        registration = {}
                    self._init_requires_session = (
                        isinstance(registration, dict) and registration.get("status_code") == 1001
                    )
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

        async def fetch(current_url: str, *, secret: bool) -> dict:
            try:
                payload = await fetch_json(
                    self.client,
                    current_url,
                    params=params or None,
                    headers=self.headers(use_secret=secret),
                )
                require_success(payload)
                if (
                    self.platform == "tiktok"
                    and self._init_requires_session
                    and not secret
                    and not (
                        payload.get("item_list") or payload.get("items") or payload.get("data")
                    )
                ):
                    raise AuthRequired("session_required")
                return payload
            except ProviderPayloadError as exc:
                # Empty body, malformed JSON, or HTML challenge is an identity
                # failure, not proof that search legitimately returned no items.
                raise AuthRequired("session_required") from exc
            except ProviderError as exc:
                # Unlike an access-token API, a guest Cookie is not considered
                # an authorization header by fetch_json. Classify 403 here.
                if isinstance(exc, AuthRequired):
                    raise AuthRequired("session_required") from exc
                if type(exc) is ProviderError and str(exc) in (
                    "provider HTTP 401",
                    "provider HTTP 403",
                ):
                    raise AuthRequired("session_required") from exc
                raise

        try:
            return await fetch(url, secret=self.used_secret)
        except AuthRequired:
            if not self.session_cookie or self.used_secret:
                raise AuthRequired("session_required") from None
            # One retry. TikTok's signature includes the actual session
            # msToken, so re-sign against the selected identity.
            self.used_secret = True
            try:
                retry_url = await secret_url_factory() if secret_url_factory else url
                return await fetch(retry_url, secret=True)
            except Exception:
                self.used_secret = False
                raise AuthRequired("session_required") from None
