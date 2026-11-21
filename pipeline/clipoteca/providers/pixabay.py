"""Pixabay official /api/videos endpoint with explicit MP4 variants."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import (
    detect_source_language,
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    SearchLimits,
    SupportStatus,
)
from .http import fetch_json, provider_client

SEARCH_ENDPOINT = "https://pixabay.com/api/videos/"


class Pixabay(Adapter):
    name = "pixabay"
    category = "stock_video"
    auth = "PIXABAY_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("PIXABAY_API_KEY", "")
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if not self.api_key:
            raise AuthRequired("PIXABAY_API_KEY is missing")
        params = {
            "key": self.api_key,
            "q": term,
            "per_page": min(200, max(3, limits.max_candidates)),
        }
        if lang in {"es", "en", "ru", "zh"}:
            params["lang"] = lang
        payload = await fetch_json(self.client, SEARCH_ENDPOINT, params=params)
        found = []
        for row in payload.get("hits", []):
            if not isinstance(row, dict) or not row.get("id"):
                continue
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=str(row["id"]),
                    term=term,
                    language=lang,
                    source_url=str(row.get("pageURL", "")),
                    source_title=str(row.get("tags", "")) or term,
                    source_language=detect_source_language(str(row.get("tags", ""))),
                    duration_seconds=row.get("duration"),
                    metadata=row,
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if candidate.metadata:
            return candidate.metadata
        if not self.api_key:
            raise AuthRequired("PIXABAY_API_KEY is missing")
        payload = await fetch_json(
            self.client,
            SEARCH_ENDPOINT,
            params={
                "key": self.api_key,
                "id": candidate.provider_id,
            },
        )
        return next(iter(payload.get("hits", [])), {})

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        row = await self.metadata(candidate)
        versions = row.get("videos") or {}
        for quality in ("large", "medium", "small", "tiny"):
            video = versions.get(quality, {})
            if not isinstance(video, dict):
                continue
            width, height = int(video.get("width") or 0), int(video.get("height") or 0)
            link = str(video.get("url", ""))
            if link.startswith("https://") and min(width, height) <= 1080 and width and height:
                return MediaRef(link, "video/mp4", int(video.get("size") or 0) or None)
        return None
