"""Pexels official Video Search API; no scraping or page download."""

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
from .http import fetch_json

SEARCH_ENDPOINT = "https://api.pexels.com/v1/videos/search"


class Pexels(Adapter):
    name = "pexels"
    category = "stock_video"
    auth = "PEXELS_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("PEXELS_API_KEY", "")
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if not self.api_key:
            raise AuthRequired("PEXELS_API_KEY is missing")
        payload = await fetch_json(
            self.client,
            SEARCH_ENDPOINT,
            headers={"Authorization": self.api_key},
            params={"query": term, "per_page": min(80, limits.max_candidates), "page": 1},
        )
        found = []
        for row in payload.get("videos", []):
            if not isinstance(row, dict) or not row.get("id"):
                continue
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=str(row["id"]),
                    term=term,
                    language=lang,
                    source_url=str(row.get("url", "")),
                    source_title=(
                        str(row.get("url") or "").rstrip("/").split("/")[-1] or str(row["id"])
                    ),
                    source_language=detect_source_language(str(row.get("url") or "")),
                    duration_seconds=row.get("duration"),
                    width=row.get("width"),
                    height=row.get("height"),
                    metadata=row,
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if candidate.metadata:
            return candidate.metadata
        if not self.api_key:
            raise AuthRequired("PEXELS_API_KEY is missing")
        return await fetch_json(
            self.client,
            f"https://api.pexels.com/v1/videos/videos/{candidate.provider_id}",
            headers={"Authorization": self.api_key},
        )

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        row = await self.metadata(candidate)
        files = []
        for item in row.get("video_files", []):
            if not isinstance(item, dict):
                continue
            if item.get("file_type") != "video/mp4" or not str(item.get("link", "")).startswith(
                "https://"
            ):
                continue
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            if not width or not height:
                continue
            files.append((item, width, height))
        if not files:
            return None
        eligible = [(item, w, h) for item, w, h in files if min(w, h) <= 1080]
        if not eligible:
            return None
        best = max(eligible, key=lambda row: (min(row[1], row[2]), row[1] * row[2]))
        return MediaRef(str(best[0]["link"]), "video/mp4")
