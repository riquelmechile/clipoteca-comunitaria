"""YouTube Data API v3 discovery, metadata and truthful search-only resolution.

The public Data API exposes video metadata, not an authorized MP4 download URL.
No watch-page scraping, yt-dlp or invented media URLs are used.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_json, provider_client

API = "https://www.googleapis.com/youtube/v3"
LANGS = {"es", "en", "zh", "ru"}


def _duration_seconds(raw: str) -> float | None:
    match = re.fullmatch(
        r"P(?:([0-9]+)D)?T?(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]+)?)S)?",
        raw,
    )
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return (
        86400 * int(days or 0)
        + 3600 * int(hours or 0)
        + 60 * int(minutes or 0)
        + float(seconds or 0)
    )


class YouTube(Adapter):
    name = "youtube"
    category = "social_video"
    auth = "YOUTUBE_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("YOUTUBE_API_KEY", "")
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        if not self.api_key:
            raise AuthRequired("YouTube Data API key missing")
        if lang not in LANGS:
            return []
        payload = await fetch_json(
            self.client,
            API + "/search",
            params={
                "part": "id,snippet",
                "q": term,
                "type": "video",
                "videoDuration": "short",
                "relevanceLanguage": lang,
                "maxResults": min(50, limits.max_candidates),
                "key": self.api_key,
            },
        )
        found: list[Candidate] = []
        for item in payload.get("items", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), dict):
                continue
            if item["id"].get("kind") not in (None, "youtube#video"):
                continue
            video_id = str(item["id"].get("videoId") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            snippet = item.get("snippet") or {}
            if not isinstance(snippet, dict):
                continue
            title = str(snippet.get("title") or video_id)
            source_language = (
                snippet.get("defaultAudioLanguage")
                or snippet.get("defaultLanguage")
                or detect_source_language(title)
            )
            source_language = str(source_language).split("-", 1)[0]
            if source_language not in LANGS:
                source_language = detect_source_language(title)
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=video_id,
                    term=term,
                    language=lang,
                    source_url="https://www.youtube.com/watch?v=" + quote(video_id),
                    source_title=title,
                    source_language=source_language,
                    metadata={"snippet": snippet},
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not self.api_key:
            raise AuthRequired("YouTube Data API key missing")
        payload = await fetch_json(
            self.client,
            API + "/videos",
            params={
                "part": "snippet,contentDetails",
                "id": candidate.provider_id,
                "key": self.api_key,
            },
        )
        item = next(
            (item for item in payload.get("items", []) if item.get("id") == candidate.provider_id),
            {},
        )
        if not isinstance(item, dict):
            return {}
        duration = _duration_seconds(str(item.get("contentDetails", {}).get("duration") or ""))
        return {
            "video": item,
            "duration_seconds": duration,
            # Exact pixel dimensions are owner-only fileDetails, not exposed by
            # public videos.list with an API key; never synthesize them.
            "width": None,
            "height": None,
        }

    async def resolve_media(
        self, candidate: Candidate
    ) -> tuple[SupportStatus, MediaRef | None, str]:
        """Separate data-only discovery from authorized media extraction."""
        return (SupportStatus.SUPPORTED_SEARCH_ONLY, None, "data_api_no_direct_media")

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        _status, media, _reason = await self.resolve_media(candidate)
        return media
