"""Internet Archive v2: relevance-first search and lightweight movie derivatives.

Ranking and derivative selection are independent, inspired by broll-mcp (MIT).
No third-party code is copied. Only archive.org HTTPS media URLs are returned.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from ..security.download import MAX_DOWNLOAD_BYTES
from .base import Adapter, Candidate, MediaRef, ProviderError, SearchLimits, SupportStatus
from .base import detect_source_language
from .http import fetch_json, provider_client

SEARCH = "https://archive.org/advancedsearch.php"
BASE_QUERY = 'mediatype:movies AND format:(h.264 OR "h.264 IA" OR "512Kb MPEG4" OR MPEG4)'
SEARCH_FIELDS = ("title", "subject", "description", "shotlist")
RESULT_FIELDS = (
    "identifier",
    "title",
    "subject",
    "description",
    "downloads",
    "collection",
    "shotlist",
    "format",
    "mediatype",
)
VIDEO_SUFFIXES = (".mp4", ".webm", ".ogv", ".m4v")
MAX_MEDIA_BYTES = MAX_DOWNLOAD_BYTES
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "de",
        "del",
        "el",
        "en",
        "for",
        "in",
        "la",
        "las",
        "los",
        "of",
        "on",
        "or",
        "the",
        "to",
        "un",
        "una",
        "y",
        "with",
        "con",
        "por",
        "para",
        "и",
        "в",
        "на",
        "с",
    }
)
LUCENE_SPECIAL = re.compile(r'([+&|!(){}\[\]^"~*?:\\/\-])')


def normalize_terms(brief: str) -> list[str]:
    """At most six informative tokens; preserve all four source alphabets."""
    words = re.findall(r"[^\W_]+", brief.casefold(), flags=re.UNICODE)
    filtered = [word for word in words if word not in STOPWORDS]
    return list(dict.fromkeys(filtered or words))[:6]


def lucene_quote(value: str) -> str:
    return LUCENE_SPECIAL.sub(lambda m: "\\" + m.group(1), value)


def build_query(terms: list[str], *, all_terms: bool) -> str:
    if not terms:
        raise ValueError("missing Internet Archive search term")
    groups = []
    for term in terms:
        quoted = lucene_quote(term)
        fields = " OR ".join(f'{field}:"{quoted}"' for field in SEARCH_FIELDS)
        groups.append(f"({fields})")
    connector = " AND " if all_terms else " OR "
    return f"{BASE_QUERY} AND ({connector.join(groups)})"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value if isinstance(item, str))
    return ""


def _score(doc: dict, terms: list[str]) -> float:
    title = _text(doc.get("title")).casefold()
    subject = _text(doc.get("subject")).casefold()
    shotlist = _text(doc.get("shotlist")).casefold()
    score = sum(
        (3 if term in title else 0) + (2 if term in subject else 0) + (1 if term in shotlist else 0)
        for term in terms
    )
    try:
        downloads = max(1, int(doc.get("downloads") or 1))
    except (TypeError, ValueError):
        downloads = 1
    score += math.log10(downloads) / 2
    if "h.264" in _text(doc.get("format")).casefold():
        score += 1
    return round(score, 6)


def _safe_video_name(name: str) -> bool:
    return (
        bool(name)
        and name.lower().endswith(VIDEO_SUFFIXES)
        and not any(part in {".", "..", ""} for part in name.split("/"))
        and not any(c in name for c in ("\\", "\x00", "?", "#"))
    )


def _file_format(entry: dict) -> str:
    return _text(entry.get("format")).casefold()


def _kind(entry: dict) -> int:
    fmt = _file_format(entry)
    name = str(entry.get("name", "")).casefold()
    if "512kb mpeg4" in fmt or "512kb" in name:
        return 2
    if "h.264" in fmt or "h264" in fmt:
        return 0
    if "mpeg4" in fmt or name.endswith((".mp4", ".m4v")):
        return 1
    return 3


def _file_size(entry: dict) -> int:
    try:
        return int(entry.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _media_url(identifier: str, name: str) -> str:
    return (
        "https://archive.org/download/" + quote(identifier, safe="") + "/" + quote(name, safe="/")
    )


def _mime(name: str) -> str:
    suffix = name.lower().rsplit(".", 1)[-1]
    return {
        "mp4": "video/mp4",
        "m4v": "video/x-m4v",
        "webm": "video/webm",
        "ogv": "video/ogg",
    }[suffix]


def select_derivatives(files: list, identifier: str) -> tuple[dict | None, dict | None]:
    """Group the original chain, prefer playable derivatives and a 512Kb scanner."""
    valid: list[dict] = []
    names = {
        str(entry.get("name")): entry
        for entry in files
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }

    def root(name: str) -> str:
        visited: set[str] = set()
        while name in names and name not in visited:
            visited.add(name)
            original = str(names[name].get("original") or "")
            if not original or original == name:
                break
            name = original
        return name

    for item in files:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        size = _file_size(item)
        if not _safe_video_name(name) or not 0 < size <= MAX_MEDIA_BYTES:
            continue
        # Archives may hold multiple recordings; preserve the original chain.
        valid.append(
            {
                "name": name,
                "size": size,
                "format": _text(item.get("format")),
                "root": root(name),
                "derived": bool(item.get("original"))
                or str(item.get("source", "")).casefold() == "derivative",
                "url": _media_url(identifier, name),
                "mime": _mime(name),
                "kind": _kind(item),
            }
        )
    if not valid:
        return None, None
    groups: dict[str, list[dict]] = {}
    for item in valid:
        groups.setdefault(item["root"], []).append(item)
    candidates = [
        min(group, key=lambda x: (not x["derived"], x["kind"], x["size"], x["name"]))
        for group in groups.values()
    ]
    chosen = min(candidates, key=lambda x: (not x["derived"], x["kind"], x["size"], x["name"]))
    scanners = [item for item in valid if item["kind"] == 2]
    scan = min(scanners, key=lambda x: (x["size"], x["name"])) if scanners else None

    def public(item: dict | None) -> dict | None:
        if item is None:
            return None
        return {key: item[key] for key in ("name", "size", "format", "url", "mime")}

    return public(chosen), public(scan)


class InternetArchive(Adapter):
    name = "internet_archive"
    category = "archive_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    rate_limit = 1

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        min_interval_seconds: float = 1.0,
    ):
        if min_interval_seconds < 0:
            raise ValueError("rate limit must be non-negative")
        self.client = client or provider_client()
        self._interval = min_interval_seconds
        self._next_request = 0.0
        self._lock = asyncio.Lock()
        self._metadata_cache: dict[str, dict] = {}

    async def _wait_for_slot(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_request:
                await asyncio.sleep(self._next_request - now)
            self._next_request = time.monotonic() + self._interval

    async def _json(self, url: str, *, params=None) -> dict:
        return await fetch_json(
            self.client,
            url,
            params=params,
            before_request=self._wait_for_slot,
            retry_statuses=(429, 503),
            max_retry_after_seconds=5.0,
        )

    async def _search_stage(self, terms: list[str], *, all_terms: bool) -> list[dict]:
        query = build_query(terms, all_terms=all_terms)
        params = [
            ("q", query),
            ("sort[]", "downloads desc"),
            ("sort[]", "identifier asc"),
            *[("fl[]", field) for field in RESULT_FIELDS],
            ("rows", "100"),
            ("page", "1"),
            ("output", "json"),
        ]
        payload = await self._json(SEARCH, params=params)
        response_params = payload.get("responseHeader", {})
        if isinstance(response_params, dict):
            echo = response_params.get("params", {})
            if isinstance(echo, dict) and "q" in echo and echo["q"] != query:
                raise ProviderError("Internet Archive returned a different query")
        response = payload.get("response", {})
        docs = response.get("docs", []) if isinstance(response, dict) else []
        if not isinstance(docs, list):
            raise ProviderError("Internet Archive returned malformed search results")
        return [item for item in docs if isinstance(item, dict) and item.get("identifier")]

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        terms = normalize_terms(term)
        if not terms:
            return []
        docs = await self._search_stage(terms, all_terms=True)
        if len(docs) < 25 and len(terms) > 1:
            broad = await self._search_stage(terms, all_terms=False)
            seen = {str(item["identifier"]) for item in docs}
            for item in broad:
                key = str(item["identifier"])
                if key not in seen:
                    seen.add(key)
                    docs.append(item)
        ranked = sorted(
            enumerate(docs),
            key=lambda pair: (-_score(pair[1], terms), pair[0]),
        )
        found = []
        for _index, item in ranked:
            identifier = str(item["identifier"])
            title = _text(item.get("title")) or identifier
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=identifier,
                    term=term,
                    language=lang,
                    source_url=f"https://archive.org/details/{quote(identifier, safe='')}",
                    source_title=title,
                    source_language=detect_source_language(title),
                    metadata={
                        "ranking_score": _score(item, terms),
                        "downloads": item.get("downloads"),
                        "scan_media": None,
                    },
                )
            )
        return found[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        identifier = candidate.provider_id
        if identifier in self._metadata_cache:
            return self._metadata_cache[identifier]
        item = await self._json(f"https://archive.org/metadata/{quote(identifier, safe='')}")
        enriched = dict(item)
        files = item.get("files", [])
        media, scan = select_derivatives(files if isinstance(files, list) else [], identifier)
        enriched["clipoteca_media"] = media
        enriched["clipoteca_scan_media"] = scan
        if len(self._metadata_cache) >= 100:
            self._metadata_cache.pop(next(iter(self._metadata_cache)))
        self._metadata_cache[identifier] = enriched
        return enriched

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        item = await self.metadata(candidate)
        selected = item.get("clipoteca_media")
        if not isinstance(selected, dict):
            return None
        return MediaRef(selected["url"], selected["mime"], selected["size"])
