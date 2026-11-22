"""VK Video Web2 adapter with cached anonymous tokens and optional own token."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx

from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_json, provider_client
from .m17_common import allowed_https, duration, walk

LOGIN = "https://login.vk.com/"
API = "https://api.vkvideo.ru/method"
CLASSIC_API = "https://api.vk.com/method"
VERSION = "5.282"
CLASSIC_VERSION = "5.199"
WEB_CLIENT_ID = "6287487"
ORIGIN = "https://vkvideo.ru"
ID = re.compile(r"-?[0-9]+_[0-9]+")
# VK Video Web2 uses OK CDN signed media URLs in public files metadata.
ROOTS = ("vkvideo.ru", "vk.com", "vkuser.net", "userapi.com", "vk-cdn.net", "okcdn.ru", "mycdn.me")
ALLOWED_HOSTS = tuple(value for root in ROOTS for value in (root, "." + root))
HLS = "application/vnd.apple.mpegurl"
_TOKEN: tuple[str, float] | None = None
_TOKEN_BACKOFF_UNTIL = 0.0
_TOKEN_LOCK = asyncio.Lock()


def _error(payload: dict) -> None:
    value = payload.get("error")
    if not value:
        return
    obj = value if isinstance(value, dict) else {}
    code = obj.get("error_code") or obj.get("code") or -1
    if code in {5, 27, 28}:
        raise AuthRequired("VK token lacks required permissions")
    if code in {6, 9, 29}:
        raise RateLimited("VK API request limit")
    raise ProviderError(f"VK API error {code}")


def _video_rows(payload: object) -> list[dict]:
    rows: list[dict] = []
    for item in walk(payload):
        identifier = item.get("id")
        owner = item.get("owner_id") or item.get("ownerId")
        if owner is not None and identifier is not None and isinstance(item.get("title"), str):
            rows.append(item)
    return rows


def _file_url(files: object) -> str:
    if not isinstance(files, dict):
        return ""
    candidates: list[tuple[int, str]] = []
    for key, value in files.items():
        if not isinstance(value, str) or not allowed_https(value, ROOTS):
            continue
        if key == "hls" or ".m3u8" in value.casefold():
            return value
        match = re.fullmatch(r"mp4_(\d+)", key)
        if match or ".mp4" in value.casefold():
            candidates.append((int(match.group(1)) if match else 0, value))
    return max(candidates, default=(0, ""))[1]


def _section_cursor(payload: object) -> tuple[str, str]:
    for item in walk(payload):
        start = item.get("next_from") or item.get("nextFrom")
        section = item.get("section_id") or item.get("sectionId")
        if start and not section:
            section = item.get("id")
        if start and section:
            return str(section), str(start)
    return "", ""


class VKVideo(Adapter):
    name = "vk_video"
    category = "social_video"
    auth = "anonymous_or_VK_ACCESS_TOKEN"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        clock=time.time,
    ):
        self.token = token if token is not None else os.getenv("VK_ACCESS_TOKEN", "")
        self.client = client or provider_client()
        self.clock = clock

    async def _token(self) -> str:
        global _TOKEN, _TOKEN_BACKOFF_UNTIL
        if self.token:
            return self.token
        if _TOKEN and self.clock() < _TOKEN[1] - 60:
            return _TOKEN[0]
        async with _TOKEN_LOCK:
            if _TOKEN and self.clock() < _TOKEN[1] - 60:
                return _TOKEN[0]
            # One failed anonymous login must not trigger four new 429s
            # from four language adapters in the same process.
            if self.clock() < _TOKEN_BACKOFF_UNTIL:
                raise RateLimited("VK anonymous login rate limited")
            try:
                payload = await fetch_json(
                    self.client,
                    LOGIN,
                    method="POST",
                    params={"act": "get_anonym_token"},
                    data={"client_id": WEB_CLIENT_ID},
                    headers={"Origin": ORIGIN, "Referer": ORIGIN + "/"},
                    max_retry_after_seconds=5.0,
                )
            except RateLimited:
                _TOKEN_BACKOFF_UNTIL = self.clock() + 15.0
                raise RateLimited("VK anonymous login rate limited") from None
            _error(payload)
            nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            token = str(
                payload.get("token")
                or payload.get("access_token")
                or nested.get("token")
                or nested.get("access_token")
                or ""
            )
            expiry = (
                payload.get("expired_at")
                or payload.get("expires_at")
                or nested.get("expired_at")
                or nested.get("expires")
            )
            try:
                expires = float(expiry)
            except (TypeError, ValueError):
                expires = self.clock() + 3600
            if not token:
                raise AuthRequired("VK anonymous token unavailable")
            _TOKEN = (token, expires)
            return token

    async def _classic_call(self, method: str, params: dict[str, Any]) -> dict:
        payload = await fetch_json(
            self.client,
            CLASSIC_API + "/" + method,
            params={**params, "access_token": self.token, "v": CLASSIC_VERSION},
        )
        _error(payload)
        result = payload.get("response")
        if not isinstance(result, dict):
            raise ProviderError("VK API response missing")
        return result

    async def _web_call(self, method: str, params: dict[str, Any]) -> dict:
        token = await self._token()
        payload = await fetch_json(
            self.client,
            API + "/" + method,
            params={**params, "access_token": token, "v": VERSION},
            headers={"Origin": ORIGIN, "Referer": ORIGIN + "/"},
        )
        _error(payload)
        result = payload.get("response")
        if not isinstance(result, dict):
            raise ProviderError("VK API response missing")
        return result

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if self.token:
            data = await self._classic_call(
                "video.search",
                {
                    "q": term,
                    "count": min(50, limits.max_candidates),
                    "adult": 0,
                    "sort": 0,
                },
            )
        else:
            data = await self._web_call(
                "catalog.getVideoSearchWeb2",
                {
                    "q": term,
                    "count": min(30, limits.max_candidates),
                    "content_type": "video",
                },
            )
        pages: list[object] = [data]
        if not self.token:
            section_id, start_from = _section_cursor(data)
            for _page in range(1, limits.max_pages):
                if not section_id or not start_from:
                    break
                page = await self._web_call(
                    "catalog.getSection",
                    {
                        "section_id": section_id,
                        "start_from": start_from,
                        "count": min(30, limits.max_candidates),
                    },
                )
                pages.append(page)
                next_section, start_from = _section_cursor(page)
                section_id = next_section or section_id
        found: list[Candidate] = []
        seen: set[str] = set()
        for item in _video_rows(pages):
            restriction = item.get("restriction")
            if item.get("content_restricted") or (
                isinstance(restriction, dict) and restriction.get("can_play") == 0
            ):
                # Anonymous Web2 search includes region-locked metadata-only rows.
                continue
            try:
                identifier = f"{int(item.get('owner_id') or item.get('ownerId'))}_{int(item['id'])}"
            except (TypeError, ValueError, KeyError):
                continue
            if identifier in seen:
                continue
            seen.add(identifier)
            title = str(item["title"])
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url=(
                        "https://vk.com/video" if self.token else "https://vkvideo.ru/video"
                    )
                    + identifier,
                    source_title=title,
                    source_language=detect_source_language(title),
                    duration_seconds=duration(item.get("duration")),
                    metadata={"provider": self.name},
                )
            )
            if len(found) >= limits.max_candidates:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not ID.fullmatch(candidate.provider_id):
            return {}
        if self.token:
            data = await self._classic_call("video.get", {"videos": candidate.provider_id})
        else:
            data = await self._web_call(
                "video.getByIds",
                {"videos": candidate.provider_id, "video_fields": "files"},
            )
        for row in _video_rows(data):
            try:
                identity = f"{int(row.get('owner_id') or row.get('ownerId'))}_{int(row['id'])}"
            except (TypeError, ValueError, KeyError):
                continue
            if identity == candidate.provider_id:
                return row
        return {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        item = await self.metadata(candidate)
        link = _file_url(item.get("files"))
        if not link:
            return None
        referer = (
            "https://vk.com/video" if self.token else "https://vkvideo.ru/video"
        ) + candidate.provider_id
        if ".m3u8" in link.casefold():
            return MediaRef(
                link,
                HLS,
                referer=referer,
                allowed_hosts=ALLOWED_HOSTS,
            )
        return MediaRef(link, "video/mp4", referer=referer, allowed_hosts=ALLOWED_HOSTS)
