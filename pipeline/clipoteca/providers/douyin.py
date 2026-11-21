"""Douyin Open Platform authorized-account video list, not public web search.

Docs: https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/search-video/account-video-list
Official video.list scope is the user's own authorized videos. No scraping.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    ProviderError,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_json, provider_client

API = "https://open.douyin.com/video/list/"


class Douyin(Adapter):
    name = "douyin"
    category = "social_video"
    auth = "DOUYIN_ACCESS_TOKEN"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self, *, token: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.token = token if token is not None else os.getenv("DOUYIN_ACCESS_TOKEN", "")
        self.client = client or provider_client()

    async def _own_videos(self, limit: int) -> list[dict]:
        if not self.token:
            raise AuthRequired("Douyin video.list OAuth token required")
        result = await fetch_json(
            self.client,
            API,
            params={"cursor": 0, "count": min(20, limit)},
            headers={"access-token": self.token},
        )
        extra = result.get("extra") or {}
        data = result.get("data") or {}
        if not isinstance(data, dict):
            raise ProviderError("Douyin data is not an object")
        error_code = data.get("error_code", 0)
        if error_code not in (0, None):
            if error_code in (2100004, 2100005, 10002):
                raise AuthRequired("Douyin unauthorized scope or token")
            raise ProviderError(f"Douyin API error {error_code}")
        if isinstance(extra, dict) and extra.get("error_code") not in (None, 0):
            raise ProviderError("Douyin API extra.error_code")
        return [item for item in (data.get("list") or []) if isinstance(item, dict)]

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        # Official API lists the authorized account, it cannot globally search
        # for arbitrary user videos. Filter owner titles only.
        rows = await self._own_videos(limits.max_candidates)
        result: list[Candidate] = []
        for item in rows:
            title = str(item.get("title") or "")
            item_id = str(item.get("item_id") or "")
            if not item_id or (term and term.casefold() not in title.casefold()):
                continue
            result.append(
                Candidate(
                    provider=self.name,
                    provider_id=item_id,
                    term=term,
                    language=lang,
                    source_url="https://www.douyin.com/video/" + item_id
                    if re.fullmatch(r"[0-9]+", item_id)
                    else "https://www.douyin.com/",
                    source_title=title or item_id,
                    source_language=detect_source_language(title),
                    metadata={"scope": "authorized_account_only"},
                )
            )
        return result

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"scope": "authorized_account_only", **candidate.metadata}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        return None
