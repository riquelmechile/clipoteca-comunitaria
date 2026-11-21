"""Smithsonian Open Access API moving-image discovery adapter."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._archive_common import first_text, nested_direct_media
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

SEARCH_ENDPOINT = "https://api.si.edu/openaccess/api/v1.0/search"


def _online_media(row: dict[str, Any]) -> list[Any]:
    content = row.get("content", {})
    if not isinstance(content, dict):
        return []
    descriptive = content.get("descriptiveNonRepeating", {})
    if not isinstance(descriptive, dict):
        return []
    online = descriptive.get("online_media", {})
    if not isinstance(online, dict):
        return []
    media = online.get("media", [])
    return media if isinstance(media, list) else []


def _has_video(row: dict[str, Any]) -> bool:
    for medium in _online_media(row):
        if not isinstance(medium, dict):
            continue
        if first_text(medium.get("type")).casefold() == "video":
            return True
        if nested_direct_media(medium):
            return True
    return False


class Smithsonian(Adapter):
    name = "smithsonian"
    category = "museum_archive_video"
    auth = "SMITHSONIAN_API_KEY"
    # Open Access commonly returns an object page and derivative images only.
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv(self.auth, "")
        self.client = client or provider_client()

    def _key(self) -> str:
        if not self.api_key:
            raise AuthRequired(f"{self.auth} is missing")
        return self.api_key

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found: list[Candidate] = []
        page_size = min(100, limits.max_candidates)
        for page in range(max(0, limits.max_pages)):
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                params={
                    "api_key": self._key(),
                    "q": f"{term} AND online_media_type:Videos",
                    "rows": page_size,
                    "start": page * page_size,
                },
            )
            response = payload.get("response", {})
            batch = response.get("rows", []) if isinstance(response, dict) else []
            if not isinstance(batch, list):
                batch = []
            for row in batch:
                if not isinstance(row, dict) or not _has_video(row):
                    continue
                provider_id = str(row.get("id") or "").strip()
                if not provider_id:
                    continue
                title = first_text(row.get("title"), provider_id)
                landing = first_text(row.get("url")) or f"https://www.si.edu/object/{provider_id}"
                direct = nested_direct_media(_online_media(row))
                metadata = dict(row)
                if direct:
                    metadata["direct_video_url"] = direct.url
                    metadata["direct_video_mime"] = direct.mime
                found.append(
                    Candidate(
                        provider=self.name,
                        provider_id=provider_id,
                        term=term,
                        language=lang,
                        source_url=landing,
                        source_title=title,
                        source_language=detect_source_language(title),
                        metadata=metadata,
                    )
                )
                if len(found) >= limits.max_candidates:
                    return found
            if not batch:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        row = await self.metadata(candidate)
        return nested_direct_media(
            {"url": row.get("direct_video_url"), "mime": row.get("direct_video_mime")}
        )
