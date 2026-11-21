"""Rutube public discovery; HLS options are not misreported as MP4 downloads.

Verified public endpoints on 2026-11-20:
  /api/search/video/?query=rain&per_page=2  -> 200 JSON
  /api/video/{id}/                    -> 200 JSON
  /api/play/options/{id}/            -> 200 JSON, video_balancer.default .m3u8
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus, detect_source_language
from .http import fetch_json

API = "https://rutube.ru/api"
ID = re.compile(r"[a-fA-F0-9]{32}")


class Rutube(Adapter):
    name = "rutube"
    category = "social_video"
    auth = "public"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        payload = await fetch_json(
            self.client,
            API + "/search/video/",
            params={"query": term, "per_page": min(50, limits.max_candidates)},
        )
        found: list[Candidate] = []
        for row in payload.get("results", []):
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("id") or "")
            if not ID.fullmatch(identifier):
                continue
            if any(row.get(flag) for flag in ("is_deleted", "is_hidden", "is_paid", "is_locked")):
                continue
            title = str(row.get("title") or identifier)
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url="https://rutube.ru/video/" + quote(identifier) + "/",
                    source_title=title,
                    source_language=detect_source_language(title),
                    duration_seconds=float(row.get("duration") or 0) or None,
                    metadata={"description": row.get("description") or "", "provider": "rutube"},
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not ID.fullmatch(candidate.provider_id):
            return {}
        payload = await fetch_json(self.client, API + "/video/" + candidate.provider_id + "/")
        return {
            "description": payload.get("description", ""),
            "duration_seconds": payload.get("duration"),
            "title": payload.get("title") or candidate.source_title,
        }

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not ID.fullmatch(candidate.provider_id):
            return None
        options = await fetch_json(
            self.client, API + "/play/options/" + candidate.provider_id + "/"
        )
        balancer = options.get("video_balancer") or {}
        if not isinstance(balancer, dict):
            return None
        # Public player options generally expose HLS; clipoteca's signed,
        # bounded single-file downloader does not process nested HLS playlists.
        # Only explicit HTTPS MP4 variants are eligible.
        for name in ("mp4", "file", "download"):
            link = str(balancer.get(name) or "")
            split = urlsplit(link)
            if split.scheme == "https" and split.hostname and split.path.lower().endswith(".mp4"):
                return MediaRef(link, "video/mp4")
        return None
