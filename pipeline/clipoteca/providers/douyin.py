"""Douyin public web video search with native Python signatures.

Search and bitrate/download URL selection adapted from Youhai020616/douyin
src/dy_cli/engines/api_client.py (MIT). See providers/signing/NOTICE.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    ProviderError,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_response, provider_client
from .m17_common import duration
from .signing import gen_false_ms_token, secsdk_sign, sign_douyin
from .web_guest import GuestSession, media_url, urls_in

VIDEO_SEARCH = "https://www.douyin.com/aweme/v1/web/search/item/"
GENERAL_SEARCH = "https://www.douyin.com/aweme/v1/web/general/search/single/"
DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
CDN_ROOTS = ("douyinvod.com", "aweme.snssdk.com")
ALLOWED_HOSTS = tuple(v for root in CDN_ROOTS for v in (root, "." + root))
_VIDEO_ID = re.compile(r"[0-9]{5,30}")


def _items(payload: dict) -> list[dict]:
    rows = payload.get("data") or payload.get("aweme_list") or payload.get("item_list") or []
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("aweme_list") or rows.get("items") or []
    if not isinstance(rows, list):
        return []
    return [
        item.get("aweme_info") if isinstance(item.get("aweme_info"), dict) else item
        for item in rows
        if isinstance(item, dict)
    ]


def _bit_rates(video: dict) -> list[dict]:
    """Adapted from Youhai DouyinAPIClient._parse_bit_rates (MIT)."""
    entries: list[dict] = []
    for br in video.get("bit_rate") or []:
        if not isinstance(br, dict):
            continue
        address = br.get("play_addr") or {}
        if not isinstance(address, dict):
            continue
        urls = urls_in(address)
        if not urls:
            continue
        width, height = int(address.get("width") or 0), int(address.get("height") or 0)
        gear = str(br.get("gear_name") or "")
        match = re.search(r"(?<!\d)(\d{3,4})(?!\d)", gear)
        resolution = int(match.group(1)) if match else min(width, height)
        entries.append(
            {
                "resolution": resolution,
                "bit_rate": int(br.get("bit_rate") or 0),
                "url": urls[-1].replace("playwm", "play"),
            }
        )
    entries.sort(key=lambda item: (item["resolution"], item["bit_rate"]), reverse=True)
    return entries


def _best_url(video: dict) -> str | None:
    """Adapted from Youhai DouyinAPIClient.get_download_url (MIT)."""
    for entry in _bit_rates(video):
        url = media_url(entry["url"], CDN_ROOTS)
        if url:
            return url
    ordinary = video.get("play_addr") or {}
    if isinstance(ordinary, dict):
        urls = urls_in(ordinary)
        if urls:
            return media_url(urls[-1], CDN_ROOTS)
    return None


class Douyin(Adapter):
    name = "douyin"
    category = "social_video"
    auth = "guest_cookie_or_optional_session"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self, *, cookie: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.client = client or provider_client()
        self.guest = GuestSession(
            self.client,
            "douyin",
            session_cookie=cookie if cookie is not None else os.getenv("DOUYIN_COOKIE", ""),
        )
        self.ms_token = gen_false_ms_token()
        self._videos: dict[str, dict] = {}

    def _params(self, term: str, *, offset: int = 0, count: int = 20, general: bool = False):
        """Port of Youhai search parameter set (MIT), signed natively."""
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "keyword": term,
            "search_channel": "aweme_general" if general else "aweme_video_web",
            "offset": str(offset),
            "count": str(count),
            "search_source": "normal_search",
            "query_correct_type": "1",
            "is_filter_search": "0",
            "need_filter_settings": "1" if offset == 0 else "0",
            "msToken": self.ms_token,
        }
        return sign_douyin(params)

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found: list[Candidate] = []
        seen: set[str] = set()
        # Video-specific search first, then general search if it has no candidates.
        for general in (False, True):
            for page in range(max(1, limits.max_pages)):
                params = self._params(term, offset=page * 20, general=general)
                payload = await self.guest.json(GENERAL_SEARCH if general else VIDEO_SEARCH, params)
                rows = _items(payload)
                for item in rows:
                    identifier = str(item.get("aweme_id") or item.get("item_id") or "")
                    if not _VIDEO_ID.fullmatch(identifier) or identifier in seen:
                        continue
                    text = str(item.get("desc") or item.get("title") or "")
                    if not text:
                        continue
                    seen.add(identifier)
                    video = item.get("video")
                    if isinstance(video, dict):
                        self._videos[identifier] = video
                    found.append(
                        Candidate(
                            provider=self.name,
                            provider_id=identifier,
                            term=term,
                            language=lang,
                            source_url=f"https://www.douyin.com/video/{identifier}",
                            source_title=text,
                            source_language=detect_source_language(text),
                            duration_seconds=duration(
                                item.get("duration") or (video or {}).get("duration")
                            ),
                            metadata={"scope": "public_search"},
                        )
                    )
                    if len(found) >= limits.max_candidates:
                        return found
                if not rows or not payload.get("has_more"):
                    break
            if found:
                break
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"title": candidate.source_title, "duration_seconds": candidate.duration_seconds}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not _VIDEO_ID.fullmatch(candidate.provider_id):
            return None
        video = self._videos.get(candidate.provider_id)
        if video is None:
            await self.guest.initialize()
            uifid = self.guest.cookie_value("uifid") or self.guest.cookie_value("UIFID")
            if not uifid:
                # Port of Youhai DouyinAPIClient._get_uifid: the homepage
                # can set the real visitor cookie or report x-tt-trace-host.
                homepage = await fetch_response(
                    self.client,
                    "https://www.douyin.com/",
                    headers=self.guest.headers(),
                )
                uifid = (
                    self.guest.cookie_value("uifid")
                    or self.guest.cookie_value("UIFID")
                    or homepage.headers.get("x-tt-trace-host", "")
                )
            if not uifid:
                raise AuthRequired("session_required")
            params = secsdk_sign(
                {"aid": "6383", "device_platform": "webapp", "aweme_id": candidate.provider_id},
                uifid,
            )
            payload = await self.guest.json(DETAIL, params)
            item = payload.get("aweme_detail") or payload.get("data") or {}
            if not isinstance(item, dict):
                raise ProviderError("Douyin video detail malformed")
            video = item.get("video") or {}
            if not isinstance(video, dict):
                return None
        url = _best_url(video)
        if not url:
            return None
        return MediaRef(url, "video/mp4", referer=candidate.source_url, allowed_hosts=ALLOWED_HOSTS)
