"""RuTube public search and bounded HLS/MP4 video resolution.

Player endpoint patterns are based on 0W-Tube (MIT, Java reference only).
Downloadable capability is conditional on M12 live probes and M16 E2E evidence.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_json, provider_client

API = "https://rutube.ru/api"
SEARCH_URL = API + "/search/video/"
ID = re.compile(r"[a-fA-F0-9]{32}")
HLS_MIME = "application/vnd.apple.mpegurl"
RUTUBE_CDN_SUFFIXES = ("rutube.ru", "rtbcdn.ru")
RUTUBE_HLS_ALLOWED_HOSTS = ("rutube.ru", ".rutube.ru", "rtbcdn.ru", ".rtbcdn.ru")
DETAIL_HTTP_CODES = frozenset({403, 429, 451})


def _allowed_rutube_host(host: str) -> bool:
    """Only allow RuTube-controlled roots or their subdomains, never lookalikes."""
    return any(host == root or host.endswith("." + root) for root in RUTUBE_CDN_SUFFIXES)



def _detail(payload: dict) -> None:
    detail = payload.get("detail")
    if not detail:
        return
    message = str(detail).casefold()
    if any(
        term in message
        for term in ("block", "limit", "denied", "429", "forbidden",
                     "too many", "restricted", "geo", "country")
    ):
        raise RateLimited("RuTube blocked this network or request")
    raise ProviderError("RuTube returned a provider error")


def _duration(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 < parsed < float("inf") else None


def _video_link(options: dict) -> str:
    balancer = options.get("video_balancer") or {}
    if isinstance(balancer, str):
        return balancer
    if not isinstance(balancer, dict):
        return ""
    values = [
        str(balancer.get(key) or "")
        for key in ("hls", "default", "m3u8", "master", "mp4", "file", "download")
    ]
    # May include both HLS and MP4; prefer HLS for scene windows.
    candidates = [value for value in values if value]
    hls = next(
        (link for link in candidates if urlsplit(link).path.casefold().endswith(".m3u8")),
        None,
    )
    return hls or next(
        (link for link in candidates if urlsplit(link).path.casefold().endswith(".mp4")),
        "",
    )


class Rutube(Adapter):
    name = "rutube"
    category = "social_video"
    auth = "public"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        pages = max(1, min(limits.max_pages, 5))
        url = SEARCH_URL
        params = {"query": term, "page": 1, "limit": 30}
        found: list[Candidate] = []
        seen: set[str] = set()
        for _page in range(pages):
            payload = await fetch_json(
                self.client, url, params=params, detail_statuses=DETAIL_HTTP_CODES
            )
            _detail(payload)
            rows = payload.get("results", [])
            if not isinstance(rows, list):
                raise ProviderError("RuTube returned invalid search results")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identifier = str(row.get("id") or "")
                if not ID.fullmatch(identifier) or identifier in seen:
                    continue
                if any(
                    row.get(flag) for flag in ("is_deleted", "is_hidden", "is_paid", "is_locked")
                ):
                    continue
                seen.add(identifier)
                title = str(row.get("title") or identifier)
                found.append(
                    Candidate(
                        provider=self.name,
                        provider_id=identifier,
                        term=term,
                        language=lang,
                        source_url="https://rutube.ru/video/" + quote(identifier) + "/",
                        source_title=title,
                        source_language=detect_source_language(title),
                        duration_seconds=_duration(row.get("duration")),
                        metadata={
                            "description": row.get("description") or "",
                            "provider": "rutube",
                            "thumbnail_url": row.get("thumbnail_url"),
                        },
                    )
                )
                if len(found) >= limits.max_candidates:
                    return found
            next_url = payload.get("next")
            if not isinstance(next_url, str) or not next_url:
                break
            resolved = urljoin(url, next_url)
            split = urlsplit(resolved)
            if (
                split.scheme != "https"
                or split.hostname != "rutube.ru"
                or split.path != "/api/search/video/"
            ):
                raise ProviderError("RuTube pagination origin is not allowed")
            url = resolved
            params = None
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not ID.fullmatch(candidate.provider_id):
            return {}
        payload = await fetch_json(
            self.client, API + "/video/" + candidate.provider_id + "/",
            detail_statuses=DETAIL_HTTP_CODES,
        )
        _detail(payload)
        return {
            "description": payload.get("description", ""),
            "duration_seconds": payload.get("duration"),
            "title": payload.get("title") or candidate.source_title,
        }

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not ID.fullmatch(candidate.provider_id):
            return None
        referer = "https://rutube.ru/video/" + candidate.provider_id + "/"
        options = await fetch_json(
            self.client,
            API + "/play/options/" + candidate.provider_id + "/",
            params={"format": "json", "no_404": "true", "mq": "all"},
            headers={"Referer": referer},
            detail_statuses=DETAIL_HTTP_CODES,
        )
        _detail(options)
        link = _video_link(options)
        try:
            split = urlsplit(link)
            port = split.port
            hostname = (split.hostname or "").casefold()
        except ValueError:
            return None
        if (
            split.scheme != "https"
            or port not in (None, 443)
            or split.username is not None
            or split.password is not None
            or not _allowed_rutube_host(hostname)
        ):
            return None
        if split.path.casefold().endswith(".m3u8"):
            return MediaRef(
                link,
                HLS_MIME,
                referer=referer,
                # Only known RuTube and RTBCDN roots are trusted for segment
                # URLs; validate each chosen CDN against real M12 smoke evidence.
                allowed_hosts=RUTUBE_HLS_ALLOWED_HOSTS,
            )
        if split.path.casefold().endswith(".mp4"):
            return MediaRef(link, "video/mp4")
        return None
