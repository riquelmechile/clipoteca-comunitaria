"""National Archives Catalog v2 video search adapter."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._archive_common import first_text, has_download_restriction, nested_direct_media
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

SEARCH_ENDPOINT = "https://catalog.archives.gov/api/v2/records/search"


def _hits(payload: dict[str, Any]) -> list[Any]:
    body = payload.get("body", payload)
    hits = body.get("hits", {}) if isinstance(body, dict) else {}
    if isinstance(hits, dict):
        return hits.get("hits", []) if isinstance(hits.get("hits"), list) else []
    return hits if isinstance(hits, list) else []


class NARA(Adapter):
    name = "nara"
    category = "government_archive_video"
    auth = "NARA_API_KEY"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv(self.auth, "")
        self.client = client or provider_client()

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AuthRequired(f"{self.auth} is missing")
        return {"x-api-key": self.api_key}

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        search_after = "*"
        found: list[Candidate] = []
        for _ in range(max(0, limits.max_pages)):
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                headers=self._headers(),
                params={
                    "q": term,
                    "limit": min(20, limits.max_candidates),
                    "searchAfter": search_after,
                },
            )
            batch = _hits(payload)
            for hit in batch:
                if not isinstance(hit, dict):
                    continue
                source = hit.get("_source", hit)
                if not isinstance(source, dict):
                    continue
                row = source.get("record", source)
                if not isinstance(row, dict):
                    continue
                provider_id = str(row.get("naId") or row.get("id") or hit.get("_id") or "").strip()
                if not provider_id:
                    continue
                title = first_text(row.get("title"), provider_id)
                landing = (
                    first_text(row.get("url")) or f"https://catalog.archives.gov/id/{provider_id}"
                )
                direct = None if has_download_restriction(row) else nested_direct_media(row)
                metadata = dict(row)
                if direct:
                    metadata["direct_video_url"] = direct.url
                    metadata["direct_video_mime"] = direct.mime
                    metadata["direct_video_size"] = direct.size_bytes
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
            body = payload.get("body", payload)
            next_after = body.get("nextSearchAfter") if isinstance(body, dict) else None
            if not next_after or next_after == search_after or not batch:
                break
            search_after = str(next_after)
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        row = await self.metadata(candidate)
        if has_download_restriction(row):
            return None
        return nested_direct_media(
            {
                "url": row.get("direct_video_url"),
                "mime": row.get("direct_video_mime"),
                "size": row.get("direct_video_size"),
            }
        )
