"""TikTok Display API v2: authorized account videos, no global public search."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ..config import USER_AGENT

from .http import provider_client
from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)

API = "https://open.tiktokapis.com/v2/video/list/"


class TikTok(Adapter):
    name = "tiktok"
    category = "social_video"
    auth = "TIKTOK_ACCESS_TOKEN"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, token: str | None = None, client: httpx.AsyncClient | None = None):
        self.token = token if token is not None else os.getenv("TIKTOK_ACCESS_TOKEN", "")
        self.client = client or provider_client()

    async def _list(self, max_results: int) -> list[dict]:
        if not self.token:
            raise AuthRequired("TikTok video.list authorization required")
        try:
            response = await self.client.post(
                API,
                params={"fields": "id,title,video_description,share_url,duration,create_time"},
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                json={"max_count": min(20, max_results)},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("TikTok Display timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("TikTok Display transport failure") from exc
        if response.status_code == 429:
            raise RateLimited("TikTok Display rate limit")
        if response.status_code in (401, 403):
            raise AuthRequired("TikTok Display token or scope missing")
        if response.status_code >= 400:
            raise ProviderError(f"TikTok Display HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("TikTok non-JSON response") from exc
        err = payload.get("error") or {}
        if isinstance(err, dict) and err.get("code") not in (None, "", "ok"):
            code = str(err["code"])
            if "token" in code or "scope" in code or "access" in code:
                raise AuthRequired("TikTok API authorization error")
            raise ProviderError("TikTok API rejected request")
        result = payload.get("data") or {}
        if not isinstance(result, dict):
            raise ProviderError("TikTok data response missing")
        return [item for item in result.get("videos", []) if isinstance(item, dict)]

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        rows = await self._list(limits.max_candidates)
        found: list[Candidate] = []
        for item in rows:
            vid = str(item.get("id") or "")
            title = str(item.get("title") or item.get("video_description") or "")
            if not re.fullmatch(r"[0-9]{5,30}", vid):
                continue
            if term and term.casefold() not in title.casefold():
                continue
            url = str(item.get("share_url") or "")
            if not url.startswith("https://www.tiktok.com/"):
                url = "https://www.tiktok.com/embed/" + vid
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=vid,
                    term=term,
                    language=lang,
                    source_url=url,
                    source_title=title or vid,
                    source_language=detect_source_language(title),
                    duration_seconds=float(item.get("duration") or 0) or None,
                    metadata={"scope": "authorized_account_only"},
                )
            )
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"scope": "authorized_account_only", **candidate.metadata}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        # TikTok Display API only gives metadata and share/embed URL.
        return None
