"""M12 candidate endpoint probes: fixed hosts, bounded responses, redacted metrics only.

Requests test public guest capabilities, not publication or permission. No token,
cookie, signed media link or exception body reaches persisted evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlparse

import httpx

from .config import USER_AGENT
from .security.ssrf import validate_public_url

TERMS = {"es": "lluvia", "en": "rain", "zh": "\u4e0b\u96e8", "ru": "\u0434\u043e\u0436\u0434\u044c"}
PROVIDERS = (
    "rutube",
    "ok_ru",
    "vk_video",
    "dzen",
    "library_of_congress",
    "douyin",
    "tiktok",
    "bilibili",
)
PERMITTED_HOSTS = frozenset(
    {
        "rutube.ru",
        "ok.ru",
        "dzen.ru",
        "login.vk.com",
        "api.vkvideo.ru",
        "www.loc.gov",
        "ttwid.bytedance.com",
        "www.douyin.com",
        "www.tiktok.com",
        "api.bilibili.com",
    }
)
LIMIT = 1_000_000
WBI_ORDER = (
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


def _obj(value):
    return value if isinstance(value, dict) else {}


def _items(value):
    return value if isinstance(value, list) else []


def _code(payload):
    data = _obj(payload)
    for key in ("status_code", "code", "error_code"):
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdecimal():
            return int(value)
    return None


def parse_metrics(provider: str, payload: object, *, html: str = "") -> dict:
    """Parse bounded fixture responses without ever retaining upstream values."""
    data = _obj(payload)
    if provider == "rutube":
        hits = _items(data.get("results"))
        stream = isinstance(data.get("video_balancer"), dict) or isinstance(
            data.get("video_balancer"), str
        )
        count = len(hits)
    elif provider == "ok_ru":
        count = len(re.findall(r"data-props\s*=", html, re.I))
        stream = False
    elif provider == "dzen":
        hits = _items(data.get("items") or _obj(data.get("data")).get("items"))
        count = len(hits)
        stream = any(
            bool(_obj(item).get("videoMetaResponse") or _obj(item).get("oneVideoStreams"))
            for item in hits
        )
    elif provider == "vk_video":
        response = _obj(data.get("response"))
        count = len(_items(response.get("items") or response.get("videos")))
        stream = any(bool(_obj(x).get("files")) for x in _items(response.get("items")))
    elif provider == "library_of_congress":
        hits = _items(data.get("results"))
        count = len(hits)
        stream = any(
            bool(_obj(_items(_obj(x).get("resources"))[0]).get("video"))
            for x in hits
            if _items(_obj(x).get("resources"))
        )
    elif provider == "douyin":
        hits = _items(data.get("data"))
        count = len(hits)
        stream = any(bool(_obj(_obj(x).get("aweme_info")).get("video")) for x in hits)
    elif provider == "tiktok":
        hits = _items(data.get("item_list") or data.get("items"))
        count = len(hits)
        stream = any(bool(_obj(x).get("video")) for x in hits)
    elif provider == "bilibili":
        hits = _items(_obj(data.get("data")).get("result"))
        count = len(hits)
        stream = False
    else:
        count, stream = 0, False
    return {
        "count": count,
        "has_stream": stream,
        "platform_code": _code(data),
        **({"search_nil_info": bool(data.get("search_nil_info"))} if provider == "douyin" else {}),
    }


def first_rutube_id(payload: object) -> str | None:
    rows = _items(_obj(payload).get("results"))
    identifier = str(_obj(rows[0]).get("id", "")) if rows else ""
    return identifier if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier) else None


def sign_wbi(params: dict[str, str], img_url: str, sub_url: str, epoch: int) -> dict[str, str]:
    """WBI algorithm based on Evil0ctal DTK (Apache-2.0); no copied source."""
    from urllib.parse import urlencode

    def stem(url: str) -> str:
        return urlparse(url).path.rsplit("/", 1)[-1].split(".")[0]

    material = stem(img_url) + stem(sub_url)
    key = "".join(material[i] for i in WBI_ORDER if i < len(material))[:32]
    if len(key) != 32:
        raise ValueError("missing wbi key")
    values = {**params, "wts": str(epoch)}
    values = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in sorted(values.items())}
    digest = hashlib.md5((urlencode(values) + key).encode()).hexdigest()  # noqa: S324
    return {**values, "w_rid": digest}


async def _request(client: httpx.AsyncClient, method: str, url: str, *, resolver=None, **kwargs):
    """One bounded HTTPS request, fixed-host and public-DNS validated.

    We disallow redirects; peer-IP pinning is not provided by httpx here,
    so candidate probes are diagnostic only, not authenticated media fetches.
    """
    host = urlparse(url).hostname
    if urlparse(url).scheme != "https" or host not in PERMITTED_HOSTS:
        raise ValueError("candidate endpoint outside allowlist")
    await validate_public_url(url, resolver=resolver)
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    async with client.stream(
        method, url, follow_redirects=False, headers=headers, **kwargs
    ) as response:
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > LIMIT:
                raise ValueError("candidate response exceeds limit")
        body = chunks.decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        # Forward only the guest ttwid, in memory. Never persist Set-Cookie.
        ttwid = None
        if host == "ttwid.bytedance.com":
            for cookie in response.headers.get_list("set-cookie"):
                match = re.search(r"(?:^|[,\s])ttwid=([a-zA-Z0-9._~%+\-]{1,512})(?:;|$)", cookie)
                if match:
                    ttwid = match.group(1)
                    break
        return response.status_code, payload, body, ttwid


async def probe_candidate(
    client: httpx.AsyncClient, provider: str, language: str, *, resolver=None
) -> list[dict]:
    term = TERMS[language]
    results = []
    guest_ttwid = None

    async def sample(stage, method, url, **kwargs):
        nonlocal guest_ttwid
        started = time.monotonic()
        row = {
            "provider": provider,
            "language": language,
            "operation": stage,
            "status_code": None,
            "platform_code": None,
            "elapsed_ms": None,
            "count": 0,
            "has_stream": False,
        }
        payload, html = {}, ""
        try:
            status, payload, html, ttwid = await _request(
                client, method, url, resolver=resolver, **kwargs
            )
            if provider in {"douyin", "tiktok"} and stage == "init":
                guest_ttwid = ttwid
            row["status_code"] = status
            row.update(parse_metrics(provider if stage != "init" else "", payload, html=html))
            if provider in {"douyin", "tiktok"} and row["platform_code"] in (2483, 1001):
                row["diagnosis"] = "session_required"
            if (
                provider == "bilibili"
                and stage == "search"
                and status == 200
                and row["count"] == 0
                and not isinstance(_obj(payload.get("data")).get("numResults"), int)
            ):
                row["diagnosis"] = "silent_block"
        except (httpx.HTTPError, ValueError, TypeError, UnicodeError) as exc:
            row["error_type"] = type(exc).__name__
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        results.append(row)
        return payload

    if provider == "rutube":
        payload = await sample(
            "search",
            "GET",
            "https://rutube.ru/api/search/video/",
            params={"query": term, "page": 1, "limit": 30},
        )
        identifier = first_rutube_id(payload)
        if identifier:
            await sample(
                "play_options",
                "GET",
                f"https://rutube.ru/api/play/options/{identifier}/",
                params={"format": "json", "no_404": "true", "mq": "all"},
                headers={"Referer": f"https://rutube.ru/video/{identifier}/"},
            )
    elif provider == "ok_ru":
        await sample(
            "search",
            "GET",
            "https://ok.ru/video/search",
            params={"st.cmd": "anonymVideo", "st.ft": "search", "st.gsq": term, "st.m": "SEARCH"},
        )
    elif provider == "dzen":
        await sample(
            "search",
            "GET",
            "https://dzen.ru/api/web/v1/zen-search",
            params={"query": term, "type_filter": "video", "page": 0},
        )
    elif provider == "vk_video":
        # Candidate web client_id is not authenticated; M12 measures its validity.
        token_payload = await sample(
            "init",
            "POST",
            "https://login.vk.com/?act=get_anonym_token",
            data={"client_id": "6287487"},
        )
        token = _obj(token_payload).get("access_token")
        if isinstance(token, str) and token:
            await sample(
                "search",
                "GET",
                "https://api.vkvideo.ru/method/catalog.getVideoSearchWeb2",
                params={
                    "v": "5.282",
                    "q": term,
                    "content_type": "video",
                    "count": "30",
                    "access_token": token,
                },
                headers={"Origin": "https://vkvideo.ru", "Referer": "https://vkvideo.ru/"},
            )
    elif provider == "library_of_congress":
        await sample(
            "search",
            "GET",
            "https://www.loc.gov/film-and-videos/",
            params={"fo": "json", "at": "results,pagination", "c": 20, "q": term},
        )
    elif provider == "douyin":
        from .providers.douyin import Douyin, VIDEO_SEARCH
        from .providers.signing import BROWSER_UA

        adapter = Douyin(cookie="", client=client)
        # Preserve the M12 stage evidence while using M19's signed search params.
        await sample(
            "init",
            "POST",
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={"region": "cn", "aid": 6383, "service": "www.douyin.com", "needFid": False},
        )
        adapter.guest.ttwid = guest_ttwid or ""
        adapter.guest._initialized = True
        await sample(
            "search",
            "GET",
            VIDEO_SEARCH,
            headers={**adapter.guest.headers(), "User-Agent": BROWSER_UA},
            params=adapter._params(term),
        )
    elif provider == "tiktok":
        from .providers.tiktok import TikTok, SEARCH
        from .providers.signing import BROWSER_UA
        from .providers.signing.tiktok_params import search_posts_params

        adapter = TikTok(cookie="", client=client)
        await sample(
            "init",
            "POST",
            "https://ttwid.bytedance.com/ttwid/union/register/",
            json={"region": "cn", "aid": 1988, "service": "www.tiktok.com", "needFid": False},
        )
        adapter.guest.ttwid = guest_ttwid or ""
        adapter.guest._initialized = True
        signed = await adapter._signed_url(
            SEARCH, search_posts_params(keyword=term, device_id=adapter.device_id)
        )
        await sample(
            "search",
            "GET",
            signed,
            headers={**adapter.guest.headers(), "User-Agent": BROWSER_UA},
        )
    elif provider == "bilibili":
        from .providers.signing import BROWSER_UA

        # Measured from Actions 2026-11-22: browser-like headers WITHOUT guest
        # buvid cookies answer the signed search; attaching them returns empty.
        headers = {
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "User-Agent": BROWSER_UA,
        }
        nav = await sample(
            "init",
            "GET",
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
        )
        wbi = _obj(_obj(nav.get("data")).get("wbi_img"))
        if isinstance(wbi.get("img_url"), str) and isinstance(wbi.get("sub_url"), str):
            try:
                params = sign_wbi(
                    {"search_type": "video", "keyword": term},
                    wbi["img_url"],
                    wbi["sub_url"],
                    int(time.time()),
                )
            except ValueError:
                return results
            await sample(
                "search",
                "GET",
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                params=params,
                headers=headers,
            )
    return results


async def candidates_smoke() -> dict:
    started = time.monotonic()
    rows = []
    timeout = httpx.Timeout(7)
    limits = httpx.Limits(max_connections=3)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # Sequential requests avoid accidental burst/rate-limit amplification.
        for provider in PROVIDERS:
            for language in TERMS:
                rows.extend(await probe_candidate(client, provider, language))
    return {
        "mode": "candidates",
        "provider_languages": rows,
        "total_probes": len(rows),
        "elapsed_seconds_measured": round(time.monotonic() - started, 3),
    }
