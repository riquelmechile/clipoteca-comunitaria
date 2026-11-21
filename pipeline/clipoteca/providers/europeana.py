"""Europeana Record API video discovery with cursor pagination."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._archive_common import first_text, media_ref
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

SEARCH_ENDPOINT = "https://api.europeana.eu/record/v2/search.json"


class Europeana(Adapter):
    name = "europeana"
    category = "cultural_archive_video"
    auth = "EUROPEANA_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv(self.auth, "")
        self.client = client or provider_client()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AuthRequired(f"{self.auth} is missing")
        return {"X-Api-Key": self.api_key}

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        cursor = "*"
        found: list[Candidate] = []
        for _ in range(max(0, limits.max_pages)):
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                headers=self._headers(),
                params={
                    "query": term,
                    "rows": min(40, limits.max_candidates),
                    "media": "true",
                    "thumbnail": "true",
                    "landingpage": "true",
                    "qf": "TYPE:VIDEO",
                    "cursor": cursor,
                },
            )
            for row in payload.get("items", []):
                if not isinstance(row, dict):
                    continue
                provider_id = str(row.get("id") or "").strip()
                if not provider_id or str(row.get("type") or "").casefold() != "video":
                    continue
                title = first_text(row.get("title"), provider_id)
                landing = first_text(row.get("edmIsShownAt"))
                if not landing:
                    landing = f"https://www.europeana.eu/item{provider_id}"
                direct = media_ref(
                    first_text(row.get("edmIsShownBy")), first_text(row.get("mimeType"))
                )
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
                        source_language=first_text(row.get("language"))
                        or detect_source_language(title),
                        metadata=metadata,
                    )
                )
                if len(found) >= limits.max_candidates:
                    return found
            next_cursor = str(payload.get("nextCursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        row = await self.metadata(candidate)
        return media_ref(row.get("direct_video_url"), row.get("direct_video_mime"))
