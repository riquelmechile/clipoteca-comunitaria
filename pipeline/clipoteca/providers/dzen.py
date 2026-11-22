"""Best-effort Dzen public video search with cookie-aware watch parsing."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .base import (
    Adapter,
    Candidate,
    MediaRef,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .base import ProviderError
from .http import ProviderPayloadError, fetch_json, fetch_response, fetch_text, provider_client
from .signing import BROWSER_UA
from .m17_common import allowed_https, duration, embedded_json, json_attributes, walk

SEARCH = "https://dzen.ru/api/web/v1/zen-search"
SEARCH_PAGE = "https://dzen.ru/search"
WATCH = "https://dzen.ru/video/watch/"
# Dzen video now streams from the VK/OK CDN (vd*.okcdn.ru), measured on a
# real watch page 2026-11-22; Yandex strm hosts kept for older videos.
ROOTS = (
    "dzen.ru",
    "strm.yandex.ru",
    "strm.yandex.net",
    "frontend.vh.yandex.ru",
    "okcdn.ru",
    "mycdn.me",
)
ALLOWED_HOSTS = tuple(value for root in ROOTS for value in (root, "." + root))
ID = re.compile(r"[A-Za-z0-9_-]{6,160}")
HLS = "application/vnd.apple.mpegurl"
WATCH_PAGE_MAX_BYTES = 8 * 1024 * 1024
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


async def _watch_document(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver=None,
    max_attempts: int = 4,
) -> str:
    """Load a watch page through Dzen's anonymous SSO handshake.

    Port of bolknote/0W-Tube DzenClient.getPage (MIT): a fresh anonymous
    session answers with SSO redirects whose only job is to set anonymous
    cookies on dzen.ru. The SSO hosts are never fetched; the cookies from the
    302 land in the client jar and the ORIGINAL URL is requested again.
    Redirects inside the exact Dzen/CDN allowlist are followed; every hop is
    DNS-checked and IP-pinned by fetch_response.
    """
    current = url
    for _attempt in range(max_attempts):
        if not allowed_https(current, ROOTS):
            raise ProviderError("Dzen redirect host not allowed")
        response = await fetch_response(
            client,
            current,
            headers={
                "Referer": "https://dzen.ru/",
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
            resolver=resolver,
            # Real watch pages embed the player state inline and exceed the
            # 2 MiB provider default (branch smoke 45048688673).
            max_bytes=WATCH_PAGE_MAX_BYTES,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise ProviderError("Dzen redirect without location")
            target = urljoin(current, location)
            host = (urlsplit(target).hostname or "").casefold()
            # SSO hop (sso.dzen.ru, sso.passport.yandex.ru or any foreign host):
            # its cookies are already stored; repeat the original page.
            sso = host.startswith("sso.") or not allowed_https(target, ROOTS)
            current = url if sso else target
            continue
        if response.status_code >= 400:
            raise ProviderError(f"Dzen watch HTTP {response.status_code}")
        body = response.text
        if "videoMetaResponse" in body or "oneVideoStreams" in body:
            return body
        if "sso.dzen.ru" not in body and "sso.passport.yandex.ru" not in body:
            return body
        current = url
    raise ProviderError("Dzen anonymous session not established")


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
        document = await _watch_document(self.client, referer)
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
                user_agent=BROWSER_UA,
            )
        return MediaRef(
            link,
            "video/mp4",
            referer=referer,
            allowed_hosts=ALLOWED_HOSTS,
            user_agent=BROWSER_UA,
        )
