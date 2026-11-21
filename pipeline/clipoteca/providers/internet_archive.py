"""Internet Archive advancedsearch/metadata; never publish item HTML."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from .base import detect_source_language, Adapter, Candidate, MediaRef, SearchLimits, SupportStatus
from .http import fetch_json, provider_client
from ..security.download import MAX_DOWNLOAD_BYTES

SEARCH = "https://archive.org/advancedsearch.php"
VIDEO_SUFFIXES = (".mp4", ".webm", ".ogv", ".m4v")

MAX_MEDIA_BYTES = MAX_DOWNLOAD_BYTES


class InternetArchive(Adapter):
    name = "internet_archive"
    category = "archive_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        escaped = term.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')
        payload = await fetch_json(
            self.client,
            SEARCH,
            params=[
                ("q", f'mediatype:movies AND (title:"{escaped}" OR description:"{escaped}")'),
                ("fl[]", "identifier"),
                ("fl[]", "title"),
                ("fl[]", "mediatype"),
                ("rows", str(min(50, limits.max_candidates))),
                ("page", "1"),
                ("output", "json"),
            ],
        )
        found = []
        for item in payload.get("response", {}).get("docs", []):
            if not isinstance(item, dict) or not item.get("identifier"):
                continue
            identifier = str(item["identifier"])
            title = item.get("title", identifier)
            if isinstance(title, list):
                title = str(title[0]) if title else identifier
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url=f"https://archive.org/details/{quote(identifier, safe='')}",
                    source_title=str(title),
                    source_language=detect_source_language(str(title)),
                    metadata=None,
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        identifier = quote(candidate.provider_id, safe="")
        return await fetch_json(self.client, f"https://archive.org/metadata/{identifier}")

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        item = await self.metadata(candidate)
        files = []
        for entry in item.get("files", []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", ""))
            if not name.lower().endswith(VIDEO_SUFFIXES):
                continue
            try:
                size = int(entry.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if not (0 < size <= MAX_MEDIA_BYTES):
                continue
            files.append((name, size))
        if not files:
            return None
        files.sort(key=lambda x: (not x[0].lower().endswith((".mp4", ".m4v")), -x[1]))
        name, size = files[0]
        url = f"https://archive.org/download/{quote(candidate.provider_id, safe='')}/{quote(name, safe='/')}"
        suffix = name.lower().rsplit(".", 1)[-1]
        mime = {"mp4": "video/mp4", "m4v": "video/x-m4v", "webm": "video/webm", "ogv": "video/ogg"}[
            suffix
        ]
        return MediaRef(url, mime, size)
