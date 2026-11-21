"""VK Video API v5.199: token-gated search and explicit media provenance."""

from __future__ import annotations

import os
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

API = "https://api.vk.com/method"
VERSION = "5.199"


class VKVideo(Adapter):
    name = "vk_video"
    category = "social_video"
    auth = "VK_ACCESS_TOKEN"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, token: str | None = None, client: httpx.AsyncClient | None = None):
        self.token = token if token is not None else os.getenv("VK_ACCESS_TOKEN", "")
        self.client = client or provider_client()

    async def _call(self, method: str, params: dict[str, Any]) -> dict:
        if not self.token:
            raise AuthRequired("VK Video access token required")
        data = await fetch_json(
            self.client,
            API + "/" + method,
            params={**params, "access_token": self.token, "v": VERSION},
        )
        if "error" in data:
            obj = data.get("error") or {}
            code = obj.get("error_code") if isinstance(obj, dict) else -1
            if code in {5, 27, 28}:
                raise AuthRequired("VK token lacks required permissions")
            if code in {6, 9}:
                raise RateLimited("VK API request limit")
            raise ProviderError(f"VK API error {code}")
        result = data.get("response")
        if not isinstance(result, dict):
            raise ProviderError("VK API response missing")
        return result

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        data = await self._call(
            "video.search",
            {
                "q": term,
                "count": min(50, limits.max_candidates),
                "adult": 0,
                "sort": 0,
            },
        )
        found: list[Candidate] = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            try:
                owner, vid = int(item["owner_id"]), int(item["id"])
            except (ValueError, TypeError, KeyError):
                continue
            title = str(item.get("title") or vid)
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=f"{owner}_{vid}",
                    term=term,
                    language=lang,
                    source_url=f"https://vk.com/video{owner}_{vid}",
                    source_title=title,
                    source_language=detect_source_language(title),
                    duration_seconds=float(item.get("duration") or 0) or None,
                    metadata={
                        "description": item.get("description", ""),
                        "files": item.get("files") or {},
                    },
                )
            )
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        data = await self._call("video.get", {"videos": candidate.provider_id})
        items = data.get("items") or []
        return items[0] if items and isinstance(items[0], dict) else {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        # Video API search can include playable MP4 variants for some items,
        # but license/rights and origin must be verified independently.
        # Keep all discovered third-party items search-only; no auto-import.
        return None
