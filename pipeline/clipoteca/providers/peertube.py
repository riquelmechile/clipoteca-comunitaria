"""Federated PeerTube search and direct Web Video file resolution.

SepiaSearch is only the discovery index. Instance metadata is fetched from an
explicitly trusted operator-maintained host allowlist, preventing arbitrary
URLs in federated results from becoming SSRF targets.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..security.download import MAX_DOWNLOAD_BYTES
from .base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus, detect_source_language
from .http import fetch_json

SEPIA_API = "https://sepiasearch.org/api/v1/search/videos"
UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


class PeerTube(Adapter):
    name = "peertube"
    category = "federated_video"
    auth = "public"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        instances: tuple[str, ...] | None = None,
        search_endpoint: str = SEPIA_API,
    ) -> None:
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        self.instances = frozenset(
            host.strip().lower()
            for host in (
                instances
                if instances is not None
                else tuple(os.getenv("PEERTUBE_ALLOWED_INSTANCES", "pt.na4.eu").split(","))
            )
            if host.strip() and re.fullmatch(r"[A-Za-z0-9.-]+", host.strip())
        )
        if not self.instances:
            raise ValueError("PeerTube requires one or more explicit safe instances")
        if search_endpoint != SEPIA_API:
            parsed = urlsplit(search_endpoint)
            if parsed.scheme != "https" or parsed.hostname not in self.instances:
                raise ValueError("custom PeerTube search endpoint must use allowed HTTPS host")
        self.search_endpoint = search_endpoint

    def _host(self, candidate: Candidate) -> str | None:
        parsed = urlsplit(candidate.source_url)
        if parsed.scheme != "https" or parsed.port is not None:
            return None
        host = (parsed.hostname or "").lower()
        return host if host in self.instances else None

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        payload = await fetch_json(
            self.client,
            self.search_endpoint,
            params={"search": term, "count": min(50, limits.max_candidates)},
        )
        result: list[Candidate] = []
        for item in payload.get("data", []):
            if not isinstance(item, dict):
                continue
            uuid = str(item.get("uuid") or "")
            if not UUID.fullmatch(uuid):
                continue
            source_url = str(item.get("url") or "")
            split = urlsplit(source_url)
            if split.scheme != "https" or (split.hostname or "").lower() not in self.instances:
                continue
            title = str(item.get("name") or uuid)
            reported_lang = item.get("language") or {}
            code = str(reported_lang.get("id") or "") if isinstance(reported_lang, dict) else ""
            code = code.split("-", 1)[0].lower()
            if code not in {"es", "en", "zh", "ru"}:
                code = detect_source_language(title)
            result.append(
                Candidate(
                    provider=self.name,
                    provider_id=uuid,
                    term=term,
                    language=lang,
                    source_url="https://" + split.hostname + "/videos/watch/" + uuid,
                    source_title=title,
                    source_language=code,
                    duration_seconds=float(item.get("duration") or 0) or None,
                    metadata={"instance": split.hostname},
                )
            )
        return result[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        host = self._host(candidate)
        if host is None or not UUID.fullmatch(candidate.provider_id):
            return {}
        payload = await fetch_json(
            self.client, f"https://{host}/api/v1/videos/{candidate.provider_id}"
        )
        return {
            "title": payload.get("name") or candidate.source_title,
            "duration_seconds": payload.get("duration"),
            "licence": payload.get("licence"),
            "files": payload.get("files", []),
            "streamingPlaylists": payload.get("streamingPlaylists", []),
        }

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        details = await self.metadata(candidate)
        all_files: list[dict] = []
        all_files.extend(item for item in details.get("files", []) if isinstance(item, dict))
        for playlist in details.get("streamingPlaylists", []):
            if isinstance(playlist, dict):
                all_files.extend(
                    item for item in playlist.get("files", []) if isinstance(item, dict)
                )
        choices: list[tuple[int, int, str, str]] = []
        for item in all_files:
            if item.get("hasVideo") is False:
                continue
            url = str(item.get("fileUrl") or "")
            parsed = urlsplit(url)
            ext = parsed.path.lower().rsplit(".", 1)[-1]
            if parsed.scheme != "https" or ext not in {"mp4", "webm"}:
                continue
            try:
                size = int(item["size"])
            except (KeyError, ValueError, TypeError):
                continue
            if not 0 < size <= MAX_DOWNLOAD_BYTES:
                continue
            resolution = item.get("resolution") or {}
            height = int(resolution.get("id") or item.get("height") or 0)
            # Prefer best up to 1080p, then smallest among equal resolutions.
            score = height if height <= 1080 else -height
            choices.append((-score, size, url, "video/mp4" if ext == "mp4" else "video/webm"))
        if not choices:
            return None
        choices.sort()
        _, _, url, mime = choices[0]
        return MediaRef(url, mime)
