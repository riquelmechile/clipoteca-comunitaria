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
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import ProviderPayloadError, fetch_json, fetch_text, provider_client
from .m17_common import allowed_https, duration, embedded_json, json_attributes, walk

SEARCH = "https://dzen.ru/api/web/v1/zen-search"
SEARCH_PAGE = "https://dzen.ru/search"
WATCH = "https://dzen.ru/video/watch/"
ROOTS = ("dzen.ru", "strm.yandex.ru", "strm.yandex.net", "frontend.vh.yandex.ru")
ALLOWED_HOSTS = tuple(value for root in ROOTS for value in (root, "." + root))
ID = re.compile(r"[A-Za-z0-9_-]{6,160}")
HLS = "application/vnd.apple.mpegurl"
STREAM_KEYS = frozenset({"hls", "streamurl", "downloadurl", "mp4", "url"})
_COOKIE_JAR = httpx.Cookies()


def _identifier(item: dict) -> str:
    return str(item.get("id") or item.get("videoId") or item.get("publicationId") or "")


def _streams(payloads: list[object]) -> str:
    links: list[str] = []
    for payload in payloads:
        for item in walk(payload):
            for key, value in item.items():
                key_name = key.casefold()
                if (
                    isinstance(value, str)
                    and (key_name in STREAM_KEYS or re.fullmatch(r"mp4_?\d+", key_name))
                    and any(ext in value.casefold() for ext in (".m3u8", ".mp4"))
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
        self.client = client or provider_client(cookies=_COOKIE_JAR)
        self.cookies = self.client.cookies

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        payloads: list[object]
        try:
            payloads = [
                await fetch_json(
                    self.client,
                    SEARCH,
                    params={"query": term, "type_filter": "video", "page": 0},
                    timeout_seconds=limits.timeout_seconds,
                )
            ]
        except ProviderPayloadError:
            # HTML fallback is only for malformed/non-JSON zen-search payloads.
            # Rate limits, authorization errors, timeouts and transport failures propagate.
            document = await fetch_text(
                self.client,
                SEARCH_PAGE,
                params={"query": term},
                timeout_seconds=limits.timeout_seconds,
            )
            payloads = embedded_json(document, '"items"')
            payloads.extend(json_attributes(document, "data-props"))
        found: list[Candidate] = []
        seen: set[str] = set()
        for item in walk(payloads):
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
        document = await fetch_text(self.client, referer, headers={"Referer": "https://dzen.ru/"})
        payloads = embedded_json(document, "videoMetaResponse")
        payloads.extend(embedded_json(document, "oneVideoStreams"))
        link = _streams(payloads)
        if not link:
            return None
        if ".m3u8" in link.casefold():
            return MediaRef(
                link,
                HLS,
                referer=referer,
                allowed_hosts=ALLOWED_HOSTS,
            )
        return MediaRef(link, "video/mp4", referer=referer, allowed_hosts=ALLOWED_HOSTS)
