"""Digital Public Library of America moving-image discovery adapter."""

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

SEARCH_ENDPOINT = "https://api.dp.la/v2/items"


def _is_moving_image(row: dict[str, Any]) -> bool:
    resource = row.get("sourceResource", {})
    resource_type = resource.get("type") if isinstance(resource, dict) else None
    values = resource_type if isinstance(resource_type, list) else [resource_type]
    return any(
        first_text(value).casefold() in {"moving image", "movingimage", "video"} for value in values
    )


class DPLA(Adapter):
    name = "dpla"
    category = "cultural_archive_video"
    auth = "DPLA_API_KEY"
    # DPLA search records usually link to contributor pages or thumbnails.
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
        for page in range(1, max(0, limits.max_pages) + 1):
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                params={
                    "api_key": self._key(),
                    "q": term,
                    "sourceResource.type": "Moving Image",
                    "page_size": min(25, limits.max_candidates),
                    "page": page,
                },
            )
            batch = payload.get("docs", [])
            if not isinstance(batch, list):
                batch = []
            for row in batch:
                if not isinstance(row, dict) or not _is_moving_image(row):
                    continue
                provider_id = str(row.get("id") or "").strip()
                if not provider_id:
                    continue
                resource = row.get("sourceResource", {})
                if not isinstance(resource, dict):
                    resource = {}
                title = first_text(resource.get("title"), provider_id)
                landing = first_text(row.get("isShownAt")) or f"https://dp.la/item/{provider_id}"
                direct = nested_direct_media(row.get("hasView", []))
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
                        source_language=first_text(resource.get("language"))
                        or detect_source_language(title),
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
