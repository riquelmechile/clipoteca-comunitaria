"""Wikimedia Commons MediaWiki API, namespace File, actual video assets."""

from __future__ import annotations

from typing import Any

import httpx

from .base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus
from .http import fetch_json

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "ClipotecaCommunity/1.0 (https://github.com/riquelmechile/clipoteca-comunitaria)"
VIDEO_MIMES = {"video/webm", "video/ogg", "video/mp4", "application/ogg"}


class WikimediaCommons(Adapter):
    name = "wikimedia_commons"
    category = "public_media"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        payload = await fetch_json(
            self.client,
            API,
            headers={"User-Agent": USER_AGENT},
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": f"filetype:video {term}",
                "gsrnamespace": 6,
                "gsrlimit": min(50, limits.max_candidates),
                "prop": "imageinfo",
                "iiprop": "url|size|mime",
            },
        )
        pages = payload.get("query", {}).get("pages", {})
        found = []
        for page in pages.values():
            if not isinstance(page, dict) or page.get("ns") != 6:
                continue
            info = next(iter(page.get("imageinfo", [])), {})
            if info.get("mime") not in VIDEO_MIMES:
                continue
            direct = str(info.get("url", ""))
            if not direct.startswith("https://"):
                continue
            title = str(page.get("title", ""))
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=str(page.get("pageid", title)),
                    term=term,
                    language=lang,
                    source_url=str(page.get("canonicalurl") or direct),
                    source_title=title.removeprefix("File:").replace("_", " "),
                    source_language=lang,
                    width=info.get("width"),
                    height=info.get("height"),
                    metadata={"page": page, "info": info},
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if candidate.metadata:
            return candidate.metadata
        payload = await fetch_json(
            self.client,
            API,
            headers={"User-Agent": USER_AGENT},
            params={
                "action": "query",
                "format": "json",
                "pageids": candidate.provider_id,
                "prop": "imageinfo",
                "iiprop": "url|size|mime",
            },
        )
        page = next(iter(payload.get("query", {}).get("pages", {}).values()), {})
        return {"page": page, "info": next(iter(page.get("imageinfo", [])), {})}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        info = (await self.metadata(candidate)).get("info", {})
        mime = info.get("mime")
        url = str(info.get("url", ""))
        if mime in VIDEO_MIMES and url.startswith("https://"):
            return MediaRef(url, str(mime), int(info.get("size") or 0) or None)
        return None
