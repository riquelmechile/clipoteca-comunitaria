"""Coverr official video catalog adapter."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from .base import Adapter, AuthRequired, Candidate, MediaRef, SearchLimits, SupportStatus
from .base import detect_source_language
from .http import fetch_json, provider_client

SEARCH_ENDPOINT = "https://api.coverr.co/videos"


def _direct_media(row: dict[str, Any]) -> MediaRef | None:
    urls = row.get("urls")
    if not isinstance(urls, dict):
        return None
    # Preview URLs are not licensed download resources. Never fall back.
    value = urls.get("mp4_download")
    if isinstance(value, str) and value.startswith("https://"):
        if urlparse(value).path.casefold().endswith(".mp4"):
            return MediaRef(value, "video/mp4")
    return None


class Coverr(Adapter):
    name = "coverr"
    category = "stock_video"
    auth = "COVERR_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("COVERR_API_KEY", "")
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if not self.api_key:
            raise AuthRequired("COVERR_API_KEY is missing")
        found: list[Candidate] = []
        for page in range(1, limits.max_pages + 1):
            page_size = min(25, limits.max_candidates - len(found))
            if page_size <= 0:
                break
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"query": term, "page": page, "page_size": page_size, "urls": "true"},
            )
            rows = payload.get("hits", payload.get("videos", []))
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict) or not row.get("id") or _direct_media(row) is None:
                    continue
                title = str(row.get("title") or row.get("name") or row["id"])
                found.append(
                    Candidate(
                        provider=self.name,
                        provider_id=str(row["id"]),
                        term=term,
                        language=lang,
                        source_url=str(row.get("url") or row.get("coverr_url") or ""),
                        source_title=title,
                        source_language=detect_source_language(title),
                        duration_seconds=row.get("duration"),
                        metadata=row,
                    )
                )
                if len(found) >= limits.max_candidates:
                    break
            if len(rows) < page_size or len(found) >= limits.max_candidates:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        return _direct_media(await self.metadata(candidate))
