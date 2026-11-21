"""VK Video Web2 adapter with cached anonymous tokens and optional own token."""

from __future__ import annotations

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
from .http import provider_client
from .m17_common import allowed_https, duration, response_json, walk

LOGIN = "https://login.vk.com/"
API = "https://api.vkvideo.ru/method"
VERSION = "5.282"
WEB_CLIENT_ID = "6287487"
ORIGIN = "https://vkvideo.ru"
ID = re.compile(r"-?[0-9]+_[0-9]+")
ROOTS = ("vkvideo.ru", "vkuser.net", "userapi.com", "vk-cdn.net")
HLS = "application/vnd.apple.mpegurl"
_TOKEN: tuple[str, float] | None = None


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
        global _TOKEN
        if self.token:
            return self.token
        if _TOKEN and self.clock() < _TOKEN[1] - 60:
            return _TOKEN[0]
        response = await self.client.post(
            LOGIN,
            params={"act": "get_anonym_token"},
            data={"client_id": WEB_CLIENT_ID},
            headers={"Origin": ORIGIN, "Referer": ORIGIN + "/"},
        )
        payload = await response_json(response, "VK token")
        nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        token = str(
            payload.get("token") or payload.get("access_token") or nested.get("token") or ""
        )
        expiry = payload.get("expired_at") or payload.get("expires_at") or nested.get("expired_at")
        try:
            expires = float(expiry)
        except (TypeError, ValueError):
            expires = self.clock() + 3600
        if not token:
            raise AuthRequired("VK anonymous token unavailable")
        _TOKEN = (token, expires)
        return token

    async def _call(self, method: str, params: dict[str, Any]) -> dict:
        token = await self._token()
        response = await self.client.get(
            API + "/" + method,
            params={**params, "access_token": token, "v": VERSION},
            headers={"Origin": ORIGIN, "Referer": ORIGIN + "/"},
        )
        payload = await response_json(response, "VK")
        _error(payload)
        result = payload.get("response")
        if not isinstance(result, dict):
            raise ProviderError("VK API response missing")
        return result

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        data = await self._call(
            "catalog.getVideoSearchWeb2",
            {
                "q": term,
                "count": min(30, limits.max_candidates),
                "content_type": "video",
            },
        )
        found: list[Candidate] = []
        seen: set[str] = set()
        for item in _video_rows(data):
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
                    source_url="https://vkvideo.ru/video" + identifier,
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
        data = await self._call(
            "video.getByIds",
            {"videos": candidate.provider_id, "video_fields": "files"},
        )
        rows = _video_rows(data)
        return rows[0] if rows else {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        item = await self.metadata(candidate)
        link = _file_url(item.get("files"))
        if not link:
            return None
        referer = "https://vkvideo.ru/video" + candidate.provider_id
        if ".m3u8" in link.casefold():
            return MediaRef(
                link,
                HLS,
                referer=referer,
                allowed_hosts=(
                    "vkvideo.ru",
                    ".vkvideo.ru",
                    "vkuser.net",
                    ".vkuser.net",
                    "userapi.com",
                    ".userapi.com",
                    "vk-cdn.net",
                    ".vk-cdn.net",
                ),
            )
        return MediaRef(link, "video/mp4", referer=referer)
