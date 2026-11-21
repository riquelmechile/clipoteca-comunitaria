"""TikTok public web search and safe MP4 resolution with native Python signing.

Search query/pagination derive from Evil0ctal/DTK v5
src/dtk/platforms/tiktok/params.py (Apache-2.0); see signing/NOTICE.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Any

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
from .m17_common import duration
from .signing import BROWSER_UA, sign_tiktok
from .signing.tiktok_params import search_posts_params, base_params
from .web_guest import GuestSession, media_url, urls_in

SEARCH = "https://www.tiktok.com/api/search/item/full/"
DETAIL = "https://www.tiktok.com/api/item/detail/"
CDN_ROOTS = (
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokv.com",
    "byteoversea.com",
    "ibytedtos.com",
)
ALLOWED_HOSTS = tuple(v for root in CDN_ROOTS for v in (root, "." + root))
_VIDEO_ID = re.compile(r"[0-9]{5,30}")
_SEARCH_ID = re.compile(r"[0-9A-Za-z]{1,64}")


def _items(payload: dict) -> list[dict]:
    data = payload.get("data") or {}
    raw = payload.get("item_list") or payload.get("items")
    if raw is None and isinstance(data, dict):
        raw = data.get("item_list") or data.get("items")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _video_url(item: dict) -> str | None:
    video = item.get("video") or {}
    if not isinstance(video, dict):
        return None
    candidates: list[str] = []
    for key in ("playAddr", "play_addr", "downloadAddr"):
        candidates.extend(urls_in(video.get(key)))
    for record in video.get("bitrateInfo") or video.get("bit_rate") or []:
        if isinstance(record, dict):
            candidates.extend(urls_in(record.get("PlayAddr") or record.get("play_addr")))
    for raw in candidates:
        safe = media_url(raw, CDN_ROOTS)
        if safe:
            return safe
    return None


class TikTok(Adapter):
    name = "tiktok"
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
            "tiktok",
            session_cookie=cookie if cookie is not None else os.getenv("TIKTOK_COOKIE", ""),
        )
        self.device_id = str(secrets.randbelow(9 * 10**18) + 10**18)
        self._videos: dict[str, dict] = {}

    async def _signed_url(self, endpoint: str, params: dict[str, str]) -> str:
        await self.guest.initialize()
        pairs = list(params.items())
        signed, _signatures = sign_tiktok(
            pairs, BROWSER_UA, ms_token=self.guest.cookie_value("msToken")
        )
        return endpoint + "?" + signed

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found: list[Candidate] = []
        seen: set[str] = set()
        cursor: str | None = None
        offset = 0
        for page in range(max(1, limits.max_pages)):
            params = search_posts_params(
                keyword=term,
                cursor=cursor,
                count=min(20, limits.max_candidates),
                device_id=self.device_id,
            )
            url = await self._signed_url(SEARCH, params)
            payload = await self.guest.json(
                url, {}, secret_url_factory=lambda: self._signed_url(SEARCH, params)
            )
            rows = _items(payload)
            for item in rows:
                identifier = str(item.get("id") or item.get("item_id") or "")
                if not _VIDEO_ID.fullmatch(identifier) or identifier in seen:
                    continue
                title = str(item.get("desc") or item.get("title") or "")
                if not title:
                    continue
                seen.add(identifier)
                self._videos[identifier] = item
                author = item.get("author") or {}
                handle = str(author.get("uniqueId") or author.get("unique_id") or "video")
                handle = re.sub(r"[^A-Za-z0-9_.]", "", handle)[:64] or "video"
                found.append(
                    Candidate(
                        provider=self.name,
                        provider_id=identifier,
                        term=term,
                        language=lang,
                        source_url=f"https://www.tiktok.com/@{handle}/video/{identifier}",
                        source_title=title,
                        source_language=detect_source_language(title),
                        duration_seconds=duration(
                            item.get("duration") or (item.get("video") or {}).get("duration")
                        ),
                        metadata={"scope": "public_search"},
                    )
                )
                if len(found) >= limits.max_candidates:
                    return found
            if not rows or not payload.get("has_more") or page + 1 >= limits.max_pages:
                break
            extra = payload.get("extra") or {}
            log = payload.get("log_pb") or {}
            # DTK v5 params.py uses the response's search_id; the
            # log_pb.impr_id is the authoritative search identity.
            search_id = str(
                (log.get("impr_id") if isinstance(log, dict) else "")
                or payload.get("search_id")
                or (extra.get("logid") if isinstance(extra, dict) else "")
                or ""
            )
            if not _SEARCH_ID.fullmatch(search_id):
                break
            offset += len(rows)
            cursor = f"{offset}:{search_id}"
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return {"title": candidate.source_title, "duration_seconds": candidate.duration_seconds}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not _VIDEO_ID.fullmatch(candidate.provider_id):
            return None
        item = self._videos.get(candidate.provider_id)
        if item is None:
            url = await self._signed_url(
                DETAIL, {**base_params(device_id=self.device_id), "itemId": candidate.provider_id}
            )
            payload = await self.guest.json(
                url,
                {},
                secret_url_factory=lambda: self._signed_url(
                    DETAIL,
                    {**base_params(device_id=self.device_id), "itemId": candidate.provider_id},
                ),
            )
            detail = payload.get("itemInfo") or payload.get("data") or {}
            if isinstance(detail, dict):
                item = detail.get("itemStruct") or detail.get("item") or detail
            if not isinstance(item, dict):
                raise ProviderError("TikTok item detail malformed")
        url = _video_url(item)
        if not url:
            return None
        return MediaRef(url, "video/mp4", referer=candidate.source_url, allowed_hosts=ALLOWED_HOSTS)
