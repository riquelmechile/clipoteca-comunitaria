"""Bilibili WBI search and signed playback manifests (best effort).

WBI algorithm reference: Evil0ctal/Douyin_TikTok_Download_API,
Apache-2.0, commit ed4ec42534b1194f8d8c18be7b4ba493334f5321.
Searches are sent without guest cookies (see _headers).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .signing import BROWSER_UA
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
# Bilibili overseas mirrors on Akamai (measured upos-*-mirrorakam hosts).
CDN_AKAMAI = re.compile(r"upos-[a-z0-9-]{1,40}\.akamaized\.net")


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
        or not (
            any(name == domain[1:] or name.endswith(domain) for domain in CDN_SUFFIXES)
            or name in CDN_EXACT
            or CDN_AKAMAI.fullmatch(name)
        )
    ):
        raise ProviderError("Bilibili media CDN host rejected")
    return name


class Bilibili(Adapter):
    name = "bilibili"
    category = "social_video"
    auth = "BILIBILI_COOKIE_optional"
    # Real download measured from GitHub Actions (smoke 45051277263): search,
    # pagelist cid, html5 playurl and Akamai mirror MP4. CDN speed varies
    # (45051899129 hit the 90 s smoke deadline), so download is best effort.
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, cookie: str | None = None, client: httpx.AsyncClient | None = None):
        self.cookie = cookie if cookie is not None else os.getenv("BILIBILI_COOKIE", "")
        self.client = client or provider_client()
        self._http_412_count = 0
        self._mixin: str | None = None
        self._keys_since = 0.0
        self._key_lock = asyncio.Lock()
        self._blocked = False

    @property
    def _headers(self) -> dict[str, str]:
        # Measured from GitHub Actions on 2026-11-22 (runs 45046484588,
        # 45047038160): the signed search answers a browser-like request
        # WITHOUT cookies in es/en/zh/ru, and answers empty once guest
        # buvid3/buvid4/b_nut cookies are attached. Header set follows
        # NanmiCoder/MediaCrawler and Evil0ctal DTK (UA, Origin, Referer).
        headers = {
            "User-Agent": os.getenv("BILIBILI_USER_AGENT") or BROWSER_UA,
            "Referer": REFERER,
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    async def _request(self, endpoint: str, query: str | dict[str, object] = ()) -> dict:
        if self._blocked:
            raise ProviderError("ip_blocked")
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
                self._blocked = True
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
        if self._mixin and time.monotonic() - self._keys_since < 86400 and not refresh:
            return self._mixin
        async with self._key_lock:
            if self._mixin and time.monotonic() - self._keys_since < 86400 and not refresh:
                return self._mixin
            payload = await self._request("/x/web-interface/nav")
            data = payload.get("data") or {}
            images = data.get("wbi_img") if isinstance(data, dict) else None
            if not isinstance(images, dict):
                raise ProviderError("Bilibili WBI navigation keys unavailable")
            self._mixin = wbi_mixin(
                _key_stem(images.get("img_url")), _key_stem(images.get("sub_url"))
            )
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
            params = {"search_type": "video", "keyword": term, "page": page}
            data = await self._signed("/x/web-interface/wbi/search/type", params)
            # A code=0 body with neither results nor numResults is not
            # evidence that a search succeeded: retry once after a short
            # pause (risk control is intermittent from datacenter egress).
            if not data.get("result") and not isinstance(data.get("numResults"), int):
                await asyncio.sleep(2)
                data = await self._signed("/x/web-interface/wbi/search/type", params)
                if not data.get("result") and not isinstance(data.get("numResults"), int):
                    raise ProviderError("Bilibili silent empty search")
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
        # Both /x/web-interface/view and the signed wbi/view (and the video
        # HTML page) answer HTTP 412 from GitHub Actions egress (probes
        # 45049259580, 45049635576), while /x/player/pagelist answers code 0
        # with the cid playurl needs. Title and duration come from search.
        response = await self._request("/x/player/pagelist", {"bvid": candidate.provider_id})
        code = response.get("code")
        if code not in (0, None):
            raise ProviderError(f"Bilibili pagelist error {code}")
        pages = response.get("data")
        first = pages[0] if isinstance(pages, list) and pages else None
        if not isinstance(first, dict):
            raise ProviderError("Bilibili pagelist payload invalid")
        meta = candidate.metadata or {}
        return {
            "title": candidate.source_title,
            "duration_seconds": first.get("duration") or meta.get("duration"),
            "description": meta.get("description"),
            "cid": first.get("cid"),
            "aid": meta.get("aid"),
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
        # Browser HTML5 mode prefers one MP4 and avoids the ffmpeg DASH mux.
        data = await self._signed(
            "/x/player/wbi/playurl",
            {
                "bvid": candidate.provider_id,
                "cid": cid,
                "qn": 64,
                "fnval": 1,
                "platform": "html5",
                "high_quality": 1,
            },
        )

        def allowed_url(primary: object, backups: object, *, mp4: bool) -> tuple[str, str] | None:
            entries = [primary]
            if isinstance(backups, (list, tuple)):
                entries.extend(backups)
            for value in entries:
                if not isinstance(value, str):
                    continue
                try:
                    if mp4 and not urlsplit(value).path.lower().endswith((".mp4", ".m4v")):
                        continue
                    return value, _media_host(value)
                except ProviderError:
                    continue
            return None

        for entry in data.get("durl") or ():
            if not isinstance(entry, dict):
                continue
            selected = allowed_url(
                entry.get("url"), entry.get("backupUrl") or entry.get("backup_url"), mp4=True
            )
            if selected:
                video, host = selected
                return MediaRef(url=video, mime="video/mp4", referer=REFERER, allowed_hosts=(host,))

        # No usable durl: request separate DASH tracks through WBI.
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
        dash = data.get("dash") or {}
        if not isinstance(dash, dict):
            return None

        def bandwidth(entry: dict) -> int:
            try:
                value = int(entry.get("bandwidth") or 0)
                return max(0, value)
            except (ValueError, TypeError, OverflowError):
                return 0

        def select_stream(streams: object) -> tuple[str, str] | None:
            if not isinstance(streams, list):
                return None
            for entry in sorted(
                (x for x in streams if isinstance(x, dict)), key=bandwidth, reverse=True
            ):
                resolved = allowed_url(
                    entry.get("baseUrl") or entry.get("base_url"),
                    entry.get("backupUrl") or entry.get("backup_url"),
                    mp4=False,
                )
                if resolved:
                    return resolved
            return None

        video = select_stream(dash.get("video"))
        audio = select_stream(dash.get("audio"))
        if video is None or audio is None:
            return None
        hosts = tuple(sorted({video[1], audio[1]}))
        return MediaRef(
            url=video[0], mime="video/mp4", referer=REFERER, allowed_hosts=hosts, audio_url=audio[0]
        )
