"""Best-effort Dzen public video search with cookie-aware watch parsing."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from .base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import provider_client
from .m17_common import allowed_https, duration, embedded_json, response_json, walk

SEARCH = "https://dzen.ru/api/web/v1/zen-search"
WATCH = "https://dzen.ru/video/watch/"
ROOTS = ("dzen.ru", "yandex.net", "yandex.ru", "strm.yandex.ru")
ID = re.compile(r"[A-Za-z0-9_-]{6,160}")
HLS = "application/vnd.apple.mpegurl"


def _identifier(item: dict) -> str:
    return str(item.get("id") or item.get("videoId") or item.get("publicationId") or "")


def _streams(payloads: list[object]) -> str:
    links: list[str] = []
    for payload in payloads:
        for item in walk(payload):
            for key, value in item.items():
                if isinstance(value, str) and (
                    key.casefold() in {"url", "hls", "streamurl", "downloadurl"}
                    or key.casefold().startswith("mp4")
                ):
                    links.append(value.replace("\\/", "/"))
    safe = [url for url in links if allowed_https(url, ROOTS)]
    return next((url for url in safe if ".m3u8" in url.casefold()), None) or next(
        (url for url in safe if ".mp4" in url.casefold()), ""
    )


class Dzen(Adapter):
    name = "dzen"
    category = "social_video"
    auth = "public_cookies"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        response = await self.client.get(
            SEARCH, params={"query": term, "type_filter": "video", "page": 0}
        )
        payload = await response_json(response, "Dzen")
        found: list[Candidate] = []
        seen: set[str] = set()
        for item in walk(payload):
            identifier = _identifier(item)
            title = item.get("title") or item.get("name")
            if not ID.fullmatch(identifier) or identifier in seen or not isinstance(title, str):
                continue
            seen.add(identifier)
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url=WATCH + quote(identifier),
                    source_title=title,
                    source_language=detect_source_language(title),
                    duration_seconds=duration(item.get("duration") or item.get("durationSeconds")),
                    metadata={"provider": self.name},
                )
            )
            if len(found) >= limits.max_candidates:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"title": candidate.source_title, "duration_seconds": candidate.duration_seconds}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not ID.fullmatch(candidate.provider_id):
            return None
        referer = WATCH + candidate.provider_id
        response = await self.client.get(referer, headers={"Referer": "https://dzen.ru/"})
        if response.status_code != 200 or response.is_redirect:
            raise ProviderError(f"Dzen watch HTTP {response.status_code}")
        payloads = embedded_json(response.text, "videoMetaResponse")
        payloads.extend(embedded_json(response.text, "oneVideoStreams"))
        link = _streams(payloads)
        if not link:
            return None
        if ".m3u8" in link.casefold():
            return MediaRef(
                link,
                HLS,
                referer=referer,
                allowed_hosts=(
                    "dzen.ru",
                    ".dzen.ru",
                    "yandex.net",
                    ".yandex.net",
                    "yandex.ru",
                    ".yandex.ru",
                    "strm.yandex.ru",
                    ".strm.yandex.ru",
                ),
            )
        return MediaRef(link, "video/mp4", referer=referer)
