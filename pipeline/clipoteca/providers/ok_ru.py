"""Best-effort anonymous OK.ru video discovery and media resolution."""

from __future__ import annotations

import json
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
from .m17_common import allowed_https, duration, json_attributes, response_json, walk

SEARCH = "https://ok.ru/video/search"
PAGE = "https://ok.ru/web-api/v2/video/fetchSearchResult"
EMBED = "https://ok.ru/videoembed/"
ROOTS = ("ok.ru", "okcdn.ru", "mycdn.me")
HLS = "application/vnd.apple.mpegurl"
ID = re.compile(r"[0-9]{4,24}")


def _rows(payloads: list[object]) -> list[dict]:
    found: list[dict] = []
    for payload in payloads:
        for item in walk(payload):
            identifier = str(item.get("id") or item.get("videoId") or item.get("movieId") or "")
            title = item.get("title") or item.get("name")
            if ID.fullmatch(identifier) and isinstance(title, str) and title:
                found.append(item)
    return found


def _media(payloads: list[object]) -> str:
    links: list[str] = []
    for payload in payloads:
        for item in walk(payload):
            for key, value in item.items():
                if isinstance(value, str) and (
                    "url" in key.casefold() or key.casefold() in {"hls", "mp4"}
                ):
                    links.append(value.replace("\\/", "/"))
            videos = item.get("videos")
            if isinstance(videos, list):
                for video in videos:
                    if isinstance(video, dict) and isinstance(video.get("url"), str):
                        links.append(video["url"].replace("\\/", "/"))
    safe = [url for url in links if allowed_https(url, ROOTS)]
    return next((url for url in safe if ".m3u8" in url.casefold()), None) or next(
        (url for url in safe if ".mp4" in url.casefold()), ""
    )


class OkRu(Adapter):
    name = "ok_ru"
    category = "social_video"
    auth = "public"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or provider_client()

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        response = await self.client.get(
            SEARCH,
            params={"st.cmd": "anonymVideo", "st.ft": "search", "st.gsq": term, "st.m": "SEARCH"},
        )
        if response.status_code != 200 or response.is_redirect:
            raise ProviderError(f"OK.ru HTTP {response.status_code}")
        payloads = json_attributes(response.text, "data-props")
        result: list[Candidate] = []
        seen: set[str] = set()
        for item in _rows(payloads):
            identifier = str(item.get("id") or item.get("videoId") or item.get("movieId"))
            if identifier in seen:
                continue
            seen.add(identifier)
            title = str(item.get("title") or item.get("name"))
            result.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url="https://ok.ru/video/" + quote(identifier),
                    source_title=title,
                    source_language=detect_source_language(title),
                    duration_seconds=duration(
                        item.get("duration")
                        or item.get("durationMs")
                        and float(item["durationMs"]) / 1000
                    ),
                    metadata={"provider": self.name},
                )
            )
            if len(result) >= limits.max_candidates:
                break
        return result

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"title": candidate.source_title, "duration_seconds": candidate.duration_seconds}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not ID.fullmatch(candidate.provider_id):
            return None
        referer = EMBED + candidate.provider_id
        response = await self.client.get(referer, headers={"Referer": "https://ok.ru/"})
        if response.status_code != 200 or response.is_redirect:
            raise ProviderError(f"OK.ru embed HTTP {response.status_code}")
        payloads = json_attributes(response.text, "data-options")
        expanded = list(payloads)
        for payload in payloads:
            for item in walk(payload):
                metadata = item.get("metadata") or item.get("flashvars.metadata")
                if isinstance(metadata, str):
                    try:
                        expanded.append(json.loads(metadata))
                    except ValueError:
                        pass
                metadata_url = item.get("metadataUrl")
                if isinstance(metadata_url, str) and allowed_https(metadata_url, ("ok.ru",)):
                    response = await self.client.get(metadata_url)
                    expanded.append(await response_json(response, "OK.ru"))
        link = _media(expanded)
        if not link:
            return None
        if ".m3u8" in link.casefold():
            return MediaRef(
                link,
                HLS,
                referer=referer,
                allowed_hosts=(
                    "ok.ru",
                    ".ok.ru",
                    "okcdn.ru",
                    ".okcdn.ru",
                    "mycdn.me",
                    ".mycdn.me",
                ),
            )
        return MediaRef(link, "video/mp4", referer=referer)
