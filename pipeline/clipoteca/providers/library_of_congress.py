"""Library of Congress public JSON search adapter."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import USER_AGENT
from .base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus, detect_source_language
from .http import fetch_json

SEARCH_ENDPOINT = "https://www.loc.gov/film-and-videos/"
MIN_REQUEST_INTERVAL_SECONDS = 0.5


def _restricted(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _iter_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    elif isinstance(value, dict):
        for key in ("url", "href", "download", "src"):
            if key in value:
                yield from _iter_values(value[key])


def _media_ref(row: dict[str, Any]) -> MediaRef | None:
    resources = row.get("resources")
    if not isinstance(resources, list) or not resources or not isinstance(resources[0], dict):
        return None
    resource = resources[0]
    if _restricted(row.get("download_restricted")) or _restricted(
        resource.get("download_restricted")
    ):
        return None
    for field in ("video", "video_stream"):
        for value in _iter_values(resource.get(field)):
            if not value.startswith("https://"):
                continue
            path = urlparse(value).path.casefold()
            if path.endswith(".mp4"):
                return MediaRef(value, "video/mp4")
            if path.endswith(".webm"):
                return MediaRef(value, "video/webm")
            if path.endswith(".ogv") or path.endswith(".ogg"):
                return MediaRef(value, "video/ogg")
            if path.endswith(".mov"):
                return MediaRef(value, "video/quicktime")
    return None


class LibraryOfCongress(Adapter):
    name = "library_of_congress"
    category = "public_archive"
    auth = "none"
    rate_limit = 120
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=45,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request_started: float | None = None
        self._pace_lock = asyncio.Lock()

    async def _pace(self) -> None:
        async with self._pace_lock:
            now = self._clock()
            if self._last_request_started is not None:
                remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - self._last_request_started)
                if remaining > 0:
                    await self._sleep(remaining)
                    now = self._clock()
            self._last_request_started = now

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found: list[Candidate] = []
        for page in range(1, limits.max_pages + 1):
            page_size = min(20, limits.max_candidates - len(found))
            if page_size <= 0:
                break
            await self._pace()
            payload = await fetch_json(
                self.client,
                SEARCH_ENDPOINT,
                params={
                    "fo": "json",
                    "at": "results,pagination",
                    "c": page_size,
                    "q": term,
                    "sp": page,
                },
            )
            rows = payload.get("results", [])
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict) or _media_ref(row) is None:
                    continue
                identifier = str(row.get("id") or row.get("url") or "").rstrip("/")
                provider_id = identifier.rsplit("/", 1)[-1]
                if not provider_id:
                    continue
                title = str(row.get("title") or provider_id)
                found.append(
                    Candidate(
                        provider=self.name,
                        provider_id=provider_id,
                        term=term,
                        language=lang,
                        source_url=str(row.get("id") or row.get("url") or ""),
                        source_title=title,
                        source_language=detect_source_language(title),
                        metadata=row,
                    )
                )
                if len(found) >= limits.max_candidates:
                    break
            if len(rows) < page_size or len(found) >= limits.max_candidates:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return candidate.metadata or {}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        return _media_ref(await self.metadata(candidate))
