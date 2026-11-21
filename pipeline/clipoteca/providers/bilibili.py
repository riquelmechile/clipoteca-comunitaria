"""Bilibili public video search, with explicit WBI/risk-control outcomes.

Search works anonymously in some regions; account verification and WBI are
subject to upstream policy. Player manifests do not imply reusable MP4 media.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)

API = "https://api.bilibili.com"
BVID = re.compile(r"BV[A-Za-z0-9]{10}")


class Bilibili(Adapter):
    name = "bilibili"
    category = "social_video"
    auth = "BILIBILI_COOKIE_optional"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, cookie: str | None = None, client: httpx.AsyncClient | None = None):
        self.cookie = cookie if cookie is not None else os.getenv("BILIBILI_COOKIE", "")
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def _get(self, endpoint: str, params: dict) -> dict:
        headers = {"User-Agent": "Mozilla/5.0 Clipoteca/1.0"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            response = await self.client.get(API + endpoint, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Bilibili timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Bilibili transport error") from exc
        if response.status_code in {412, 429}:
            raise RateLimited("Bilibili anti-bot or HTTP rate limit")
        if response.status_code >= 400:
            raise ProviderError(f"Bilibili HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Bilibili non-JSON response") from exc
        if not isinstance(data, dict):
            raise ProviderError("Bilibili response not object")
        code = data.get("code", 0)
        if code in {-352, -412}:
            raise RateLimited("Bilibili risk control")
        if code not in {0, None}:
            raise ProviderError(f"Bilibili error code {code}")
        return data.get("data") or {}

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        data = await self._get(
            "/x/web-interface/search/type",
            {"search_type": "video", "keyword": term, "page": 1},
        )
        results: list[Candidate] = []
        for item in data.get("result") or []:
            if not isinstance(item, dict) or item.get("is_pay") or item.get("is_charge_video"):
                continue
            bvid = str(item.get("bvid") or "")
            if not BVID.fullmatch(bvid):
                continue
            title = re.sub(r"<[^>]+>", "", html.unescape(str(item.get("title") or bvid)))
            results.append(
                Candidate(
                    provider=self.name,
                    provider_id=bvid,
                    term=term,
                    language=lang,
                    source_url="https://www.bilibili.com/video/" + quote(bvid),
                    source_title=title,
                    source_language=detect_source_language(title),
                    metadata={
                        "description": item.get("description", ""),
                        "duration": item.get("duration"),
                    },
                )
            )
            if len(results) >= limits.max_candidates:
                break
        return results

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not BVID.fullmatch(candidate.provider_id):
            return {}
        payload = await self._get("/x/web-interface/view", {"bvid": candidate.provider_id})
        return {
            "title": payload.get("title") or candidate.source_title,
            "duration_seconds": payload.get("duration"),
            "description": payload.get("desc"),
            "cid": payload.get("cid"),
        }

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        # Available playback APIs return DASH/HLS segmented resources and
        # routinely require authenticated signatures. Not a reusable MP4.
        return None
