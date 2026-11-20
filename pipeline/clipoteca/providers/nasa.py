"""NASA Images and Video Library: discover video, resolve /asset manifest to MP4."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from .base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus
from .http import fetch_json

API = "https://images-api.nasa.gov"


class NASA(Adapter):
    name = "nasa"
    category = "space_video"
    auth = "NASA_API_KEY_optional"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("NASA_API_KEY", "DEMO_KEY")
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        payload = await fetch_json(
            self.client,
            API + "/search",
            params={"q": term, "media_type": "video", "page_size": min(100, limits.max_candidates)},
        )
        found = []
        for item in payload.get("collection", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            data = next(iter(item.get("data", [])), {})
            nasa_id = str(data.get("nasa_id", ""))
            if not nasa_id or data.get("media_type") != "video":
                continue
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=nasa_id,
                    term=term,
                    language=lang,
                    source_url=str(
                        item.get("href")
                        or f"https://images.nasa.gov/details/{quote(nasa_id, safe='')}"
                    ),
                    source_title=str(data.get("title") or nasa_id),
                    source_language=lang,
                    metadata={"data": data, "item": item},
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if candidate.metadata:
            return candidate.metadata
        payload = await fetch_json(
            self.client,
            API + "/search",
            params={"nasa_id": candidate.provider_id, "media_type": "video"},
        )
        item = next(iter(payload.get("collection", {}).get("items", [])), {})
        return {"data": next(iter(item.get("data", [])), {}), "item": item}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        manifest = await fetch_json(
            self.client, API + "/asset/" + quote(candidate.provider_id, safe="")
        )
        urls = []
        for item in manifest.get("collection", {}).get("items", []):
            url = str(item.get("href") or "")
            if url.startswith("https://") and url.lower().split("?", 1)[0].endswith(".mp4"):
                urls.append(url)
        if not urls:
            return None
        # Prefer a smaller publicly documented rendition when present.
        urls.sort(key=lambda value: ("~orig" in value.lower(), len(value)))
        return MediaRef(urls[0], "video/mp4")
