"""Bilibili WBI search and signed playback manifests (best effort).

WBI algorithm reference: Evil0ctal/Douyin_TikTok_Download_API,
Apache-2.0, commit ed4ec42534b1194f8d8c18be7b4ba493334f5321.
Only public navigation keys are used. No cookie is required.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ..config import USER_AGENT
from .base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import provider_client

API = "https://api.bilibili.com"
REFERER = "https://www.bilibili.com/"
BVID = re.compile(r"BV[A-Za-z0-9]{10}")
MIXIN_KEY_ORDER = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)
CDN_SUFFIXES = (".bilivideo.com", ".bilivideo.cn", ".hdslb.com")
CDN_EXACT = {"upos-hz-mirrorakam.akamaized.net"}


def _key_stem(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderError("Bilibili WBI key missing")
    filename = urlsplit(value).path.rsplit("/", 1)[-1].split(".", 1)[0]
    if not re.fullmatch(r"[a-zA-Z0-9]{32}", filename):
        raise ProviderError("Bilibili WBI key invalid")
    return filename


def wbi_mixin(image_key: str, sub_key: str) -> str:
    raw = image_key + sub_key
    if len(raw) != 64:
        raise ValueError("invalid WBI key length")
    return "".join(raw[i] for i in MIXIN_KEY_ORDER)[:32]


def _strip_wbi(value: str) -> str:
    return "".join(char for char in value if char not in "!'()*")


def wbi_sign(params: dict[str, object], mixin_key: str, *, timestamp: int) -> tuple[str, str]:
    """Return signed query and MD5, same encodeURIComponent wire string as upstream."""
    source = {**params, "wts": str(timestamp)}
    text = "&".join(
        f"{quote(str(key), safe='')}={quote(_strip_wbi(str(value)), safe='')}"
        for key, value in sorted(source.items())
    )
    digest = hashlib.md5((text + mixin_key).encode("utf-8")).hexdigest()
    return text + "&w_rid=" + digest, digest


def _media_host(url: str) -> str:
    try:
        parts = urlsplit(url)
        name = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ProviderError("Bilibili media CDN host rejected") from exc
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or (port not in (None, 443))
        or not (any(name.endswith(domain) for domain in CDN_SUFFIXES) or name in CDN_EXACT)
    ):
        raise ProviderError("Bilibili media CDN host rejected")
    return name


class Bilibili(Adapter):
    name = "bilibili"
    category = "social_video"
    auth = "BILIBILI_COOKIE_optional"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, cookie: str | None = None, client: httpx.AsyncClient | None = None):
        self.cookie = cookie if cookie is not None else os.getenv("BILIBILI_COOKIE", "")
        self.client = client or provider_client()
        self._http_412_count = 0
        self._mixin: str | None = None
        self._keys_since = 0.0

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def _request(self, endpoint: str, query: str | dict[str, object] = ()) -> dict:
        url = API + endpoint
        if isinstance(query, str):
            url += "?" + query
            params = None
        else:
            params = dict(query)
        try:
            response = await self.client.get(
                url, params=params, headers=self._headers, follow_redirects=False
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("Bilibili timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Bilibili transport failed") from exc
        if response.status_code == 412:
            self._http_412_count += 1
            if self._http_412_count >= 3:
                raise ProviderError("ip_blocked")
            raise RateLimited("Bilibili HTTP 412 risk control")
        self._http_412_count = 0
        if response.status_code == 429:
            raise RateLimited("Bilibili HTTP rate limited")
        if response.status_code >= 400:
            raise ProviderError(f"Bilibili HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Bilibili invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Bilibili response not object")
        return payload

    async def _keys(self, *, refresh: bool = False) -> str:
        age = time.monotonic() - self._keys_since
        if self._mixin and age < 86400 and not refresh:
            return self._mixin
        payload = await self._request("/x/web-interface/nav")
        data = payload.get("data") or {}
        images = data.get("wbi_img") if isinstance(data, dict) else None
        if not isinstance(images, dict):
            raise ProviderError("Bilibili WBI navigation keys unavailable")
        self._mixin = wbi_mixin(_key_stem(images.get("img_url")), _key_stem(images.get("sub_url")))
        self._keys_since = time.monotonic()
        return self._mixin

    async def _signed(self, endpoint: str, params: dict[str, object]) -> dict:
        for attempt in range(2):
            mixin = await self._keys(refresh=bool(attempt))
            query, _ = wbi_sign(params, mixin, timestamp=int(time.time()))
            result = await self._request(endpoint, query)
            code = result.get("code", 0)
            if code in (-352, -403) and attempt == 0:
                self._mixin = None
                continue
            if code in (-352, -403, -412):
                raise RateLimited("Bilibili WBI risk control")
            if code not in (0, None):
                raise ProviderError(f"Bilibili API error {code}")
            data = result.get("data")
            if not isinstance(data, dict):
                raise ProviderError("Bilibili payload invalid")
            return data
        raise RateLimited("Bilibili WBI expired")

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        results = []
        seen: set[str] = set()
        for page in range(1, min(limits.max_pages, 2) + 1):
            data = await self._signed(
                "/x/web-interface/wbi/search/type",
                {"search_type": "video", "keyword": term, "page": page},
            )
            for item in data.get("result") or []:
                if not isinstance(item, dict) or item.get("is_pay") or item.get("is_charge_video"):
                    continue
                bvid = str(item.get("bvid") or "")
                if not BVID.fullmatch(bvid) or bvid in seen:
                    continue
                seen.add(bvid)
                title = re.sub(r"<[^>]+>", "", html.unescape(str(item.get("title") or bvid)))
                results.append(
                    Candidate(
                        provider=self.name,
                        provider_id=bvid,
                        term=term,
                        language=lang,
                        source_url="https://www.bilibili.com/video/" + quote(bvid),
                        source_title=title,
                        source_language=detect_source_language(title),
                        metadata={
                            "description": item.get("description", ""),
                            "duration": item.get("duration"),
                            "aid": item.get("aid"),
                            "cid": item.get("cid"),
                        },
                    )
                )
                if len(results) >= limits.max_candidates:
                    return results
            if not data.get("result"):
                break
        return results

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if not BVID.fullmatch(candidate.provider_id):
            return {}
        response = await self._request("/x/web-interface/view", {"bvid": candidate.provider_id})
        code = response.get("code")
        if code not in (0, None):
            raise ProviderError(f"Bilibili view error {code}")
        data = response.get("data") or {}
        if not isinstance(data, dict):
            raise ProviderError("Bilibili view payload invalid")
        return {
            "title": data.get("title") or candidate.source_title,
            "duration_seconds": data.get("duration"),
            "description": data.get("desc"),
            "cid": data.get("cid"),
            "aid": data.get("aid"),
        }

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        if not BVID.fullmatch(candidate.provider_id):
            return None
        fields = dict(candidate.metadata or {})
        if not fields.get("cid"):
            fields.update(await self.metadata(candidate))
        cid = fields.get("cid")
        if not isinstance(cid, int) or cid <= 0:
            return None
        data = await self._signed(
            "/x/player/wbi/playurl",
            {
                "bvid": candidate.provider_id,
                "cid": cid,
                "qn": 64,
                "fnval": 16,
                "platform": "pc",
            },
        )
        # Prefer a regular MP4 when the player offers one.
        durl = data.get("durl") or []
        for item in durl:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            video = item["url"]
            if not urlsplit(video).path.lower().endswith((".mp4", ".m4v")):
                continue
            host = _media_host(video)
            return MediaRef(url=video, mime="video/mp4", referer=REFERER, allowed_hosts=(host,))
        dash = data.get("dash") or {}
        if not isinstance(dash, dict):
            return None
        video_streams = dash.get("video") or []
        audio_streams = dash.get("audio") or []
        if not video_streams or not audio_streams:
            return None
        video_streams = sorted(
            (entry for entry in video_streams if isinstance(entry, dict)),
            key=lambda entry: int(entry.get("bandwidth") or 0),
            reverse=True,
        )
        audio_streams = sorted(
            (entry for entry in audio_streams if isinstance(entry, dict)),
            key=lambda entry: int(entry.get("bandwidth") or 0),
            reverse=True,
        )
        if not video_streams or not audio_streams:
            return None
        video = str(video_streams[0].get("baseUrl") or video_streams[0].get("base_url") or "")
        audio = str(audio_streams[0].get("baseUrl") or audio_streams[0].get("base_url") or "")
        if not video or not audio:
            return None
        hosts = tuple(sorted({_media_host(video), _media_host(audio)}))
        return MediaRef(
            url=video, mime="video/mp4", referer=REFERER, allowed_hosts=hosts, audio_url=audio
        )
