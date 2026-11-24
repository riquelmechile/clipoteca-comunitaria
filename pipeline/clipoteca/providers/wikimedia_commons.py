"""Wikimedia Commons MediaWiki API, namespace File, actual video assets."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any
from urllib.parse import quote

import httpx

from ..config import USER_AGENT
from ..place_names import _words, aliases, query_names
from .base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    ProviderTimeout,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)
from .http import fetch_json, provider_client

API = "https://commons.wikimedia.org/w/api.php"
VIDEO_MIMES = {"video/webm", "video/ogg", "video/mp4", "application/ogg"}
MAX_CATEGORIES = 6
MAX_DEPTH = 2
MAX_MEMBERS = 50
MAX_PLACE_SECONDS = 3.0
_IMAGE_PROPS = {
    "prop": "imageinfo|info",
    "inprop": "url",
    "iiprop": "url|size|mime|extmetadata",
}


def _category_key(title: str) -> str:
    return " ".join(_words(title.removeprefix("Category:")))


def _video_category(title: str) -> bool:
    return _category_key(title).startswith("videos of ")


class WikimediaCommons(Adapter):
    name = "wikimedia_commons"
    category = "public_media"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, client: httpx.AsyncClient | None = None):
        self.client = client or provider_client()
        self._request_lock = asyncio.Lock()

    async def _query(self, **params):
        # Serialize requests on this adapter, including across concurrent scenes.
        async with self._request_lock:
            payload = await fetch_json(
                self.client,
                API,
                headers={"User-Agent": USER_AGENT},
                params={"action": "query", "format": "json", "maxlag": 5, **params},
            )
        if "error" in payload:
            raise ProviderError("MediaWiki API error")
        return payload

    async def _place_candidates(self, term, lang, limits, found):
        names = sorted(
            set(limits.place_names) | query_names([term]),
            key=lambda name: (-len(name.split()), name),
        )
        roots = {}
        # Prefer exact category titles over fuzzy search hits. In particular,
        # a category for a similarly named object is not geographic evidence.
        searched = set()
        for name in names:
            variants = aliases(name)
            if variants in searched:
                continue
            if len(searched) >= 2:
                break
            searched.add(variants)
            expression = " OR ".join(f'"{variant}"' for variant in variants)
            payload = await self._query(
                list="search", srnamespace=14, srsearch=expression, srlimit=12
            )
            for hit in payload.get("query", {}).get("search", []):
                title = hit.get("title", "")
                key = _category_key(title).removeprefix("videos of ")
                if title.startswith("Category:") and key in variants:
                    roots[title] = aliases(key)

        queue = deque(
            (title, 0, roots[title])
            for title in sorted(roots, key=lambda title: (not _video_category(title), title))
        )
        visited = set()
        seen = set()
        while queue and len(visited) < MAX_CATEGORIES and len(found) < limits.max_candidates:
            title, depth, evidence = queue.popleft()
            if title in visited:
                continue
            visited.add(title)
            if depth < MAX_DEPTH:
                payload = await self._query(
                    list="categorymembers",
                    cmtitle=title,
                    cmtype="subcat",
                    cmlimit=MAX_MEMBERS,
                )
                children = [
                    member["title"]
                    for member in payload.get("query", {}).get("categorymembers", [])
                    if member.get("ns") == 14
                    and str(member.get("title", "")).startswith("Category:")
                ]
                # Video categories are visited before general descendants.
                for child in sorted(children, key=lambda item: (not _video_category(item), item)):
                    entry = (child, depth + 1, evidence)
                    if _video_category(child):
                        queue.appendleft(entry)
                    else:
                        queue.append(entry)
            payload = await self._query(
                generator="categorymembers",
                gcmtitle=title,
                gcmtype="file",
                gcmlimit=min(MAX_MEMBERS, limits.max_candidates),
                **_IMAGE_PROPS,
            )
            for candidate in self._candidates(
                payload, term, lang, category=title, evidence=evidence
            ):
                if candidate.origin_id not in seen:
                    found.append(candidate)
                    seen.add(candidate.origin_id)
                    if len(found) >= limits.max_candidates:
                        return

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found = []
        deadline = asyncio.get_running_loop().time() + limits.timeout_seconds
        if limits.place_names or query_names([term]):
            try:
                async with asyncio.timeout(min(MAX_PLACE_SECONDS, limits.timeout_seconds / 2)):
                    await self._place_candidates(term, lang, limits, found)
            except (ProviderError, TimeoutError, TypeError, AttributeError, KeyError):
                # A category failure must not disable ordinary text search.
                pass
        if len(found) < limits.max_candidates:
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                # Reserve a little time to return candidates before the engine's own timeout.
                async with asyncio.timeout(max(0.001, remaining - 0.05)):
                    payload = await self._query(
                        generator="search",
                        gsrsearch=f"filetype:video {term}",
                        gsrnamespace=6,
                        gsrlimit=min(50, limits.max_candidates),
                        **_IMAGE_PROPS,
                    )
            except TimeoutError as exc:
                if not found:
                    raise ProviderTimeout("provider timeout") from exc
            except ProviderError:
                if not found:
                    raise
            else:
                seen = {candidate.origin_id for candidate in found}
                for candidate in self._candidates(payload, term, lang):
                    if candidate.origin_id not in seen:
                        found.append(candidate)
                        seen.add(candidate.origin_id)
        return found[: limits.max_candidates]

    def _candidates(self, payload, term, lang, *, category=None, evidence=()):
        pages = payload.get("query", {}).get("pages", {})
        found = []
        for page in pages.values():
            if not isinstance(page, dict) or page.get("ns") != 6:
                continue
            info = next(iter(page.get("imageinfo", [])), {})
            if info.get("mime") not in VIDEO_MIMES:
                continue
            direct = str(info.get("url", ""))
            if not direct.startswith("https://"):
                continue
            title = str(page.get("title", ""))
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=str(page.get("pageid", title)),
                    term=term,
                    language=lang,
                    source_url=str(
                        page.get("fullurl")
                        or page.get("canonicalurl")
                        or "https://commons.wikimedia.org/wiki/"
                        + quote(title.replace(" ", "_"), safe=":()")
                    ),
                    source_title=title.removeprefix("File:").replace("_", " "),
                    source_language=detect_source_language(title),
                    width=info.get("width"),
                    height=info.get("height"),
                    metadata={
                        "page": page,
                        "info": info,
                        **(
                            {
                                "commons_category": category.removeprefix("Category:"),
                                "commons_place_aliases": list(evidence),
                            }
                            if category
                            else {}
                        ),
                    },
                )
            )
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        if candidate.metadata:
            return candidate.metadata
        payload = await self._query(
            pageids=candidate.provider_id,
            prop="imageinfo",
            iiprop="url|size|mime",
        )
        page = next(iter(payload.get("query", {}).get("pages", {}).values()), {})
        return {"page": page, "info": next(iter(page.get("imageinfo", [])), {})}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        info = (await self.metadata(candidate)).get("info", {})
        mime = info.get("mime")
        url = str(info.get("url", ""))
        if mime in VIDEO_MIMES and url.startswith("https://"):
            return MediaRef(url, str(mime), int(info.get("size") or 0) or None)
        return None
