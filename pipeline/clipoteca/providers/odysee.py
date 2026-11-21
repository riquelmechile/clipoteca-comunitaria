"""Odysee's official clients: Lighthouse -> claim_search -> resolve/get.

Real public endpoint responses verified 2026-11-20. Data API can yield a
streaming MP4 via SDK 'get', but a claim might be larger than the safe cap.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ..config import USER_AGENT

from ..security.download import MAX_DOWNLOAD_BYTES
from .http import provider_client
from .base import (
    Adapter,
    AuthRequired,
    Candidate,
    MediaRef,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
    SupportStatus,
    detect_source_language,
)

LIGHTHOUSE = "https://lighthouse.odysee.tv/search"
PROXY = "https://api.na-backend.odysee.com/api/v1/proxy"
CLAIM_ID = re.compile(r"[0-9a-fA-F]{40}")


class Odysee(Adapter):
    name = "odysee"
    category = "decentralized_video"
    auth = "public"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or provider_client()

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        for attempt in range(3):
            try:
                headers = httpx.Headers(kwargs.pop("headers", None) or {})
                headers.setdefault("User-Agent", USER_AGENT)
                response = await self.client.request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                raise ProviderTimeout("Odysee request timed out") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("Odysee request failed") from exc
            if response.status_code == 429:
                if attempt == 2:
                    raise RateLimited("Odysee rate limit")
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            if response.status_code == 401:
                raise AuthRequired("Odysee endpoint requires authentication")
            if response.status_code >= 400:
                raise ProviderError(f"Odysee HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError("Odysee returned invalid JSON") from exc
        raise AssertionError("unreachable")

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict:
        payload = await self._request(
            "POST",
            PROXY + "?m=" + method,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={"Content-Type": "application/json"},
        )
        if not isinstance(payload, dict) or payload.get("error"):
            raise ProviderError(f"Odysee RPC {method} failed")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ProviderError(f"Odysee RPC {method} returned no object")
        return result

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        found = await self._request(
            "GET",
            LIGHTHOUSE,
            params={
                "s": term,
                "size": min(50, limits.max_candidates),
                "from": 0,
                "claimType": "file",
                "nsfw": "false",
                "free_only": "true",
            },
        )
        if isinstance(found, dict):
            found = found.get("results", [])
        if not isinstance(found, list):
            raise ProviderError("Odysee search payload must be an array")
        ids = [
            str(item.get("claimId"))
            for item in found
            if isinstance(item, dict) and CLAIM_ID.fullmatch(str(item.get("claimId") or ""))
        ]
        if not ids:
            return []
        hydrated = await self._rpc(
            "claim_search",
            {
                "claim_ids": ids,
                "claim_type": ["stream"],
                "stream_types": ["video"],
                "has_source": True,
                "page_size": len(ids),
            },
        )
        items = hydrated.get("items") or []
        results: list[Candidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("claim_id") or "")
            if not CLAIM_ID.fullmatch(claim_id):
                continue
            value = item.get("value") or {}
            if not isinstance(value, dict):
                continue
            name = str(item.get("name") or claim_id)
            title = str(value.get("title") or name)
            languages = value.get("languages") or []
            source_lang = str(languages[0]).split("-", 1)[0] if languages else ""
            if source_lang not in {"es", "en", "zh", "ru"}:
                source_lang = detect_source_language(title)
            source = value.get("source") or {}
            try:
                source_size = int(source.get("size") or 0)
            except (TypeError, ValueError):
                source_size = 0
            video = value.get("video") or {}
            uri = str(item.get("permanent_url") or "")
            if not uri.startswith("lbry://"):
                continue
            results.append(
                Candidate(
                    provider=self.name,
                    provider_id=claim_id,
                    term=term,
                    language=lang,
                    source_url="https://odysee.com/" + quote(name, safe="-_") + ":" + claim_id[:12],
                    source_title=title,
                    source_language=source_lang,
                    duration_seconds=float(video.get("duration") or 0) or None,
                    metadata={
                        "uri": uri,
                        "size_bytes": source_size,
                        "description": value.get("description", ""),
                        "license": value.get("license", ""),
                    },
                )
            )
        return results[: limits.max_candidates]

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        uri = str((candidate.metadata or {}).get("uri") or "")
        if not uri.startswith("lbry://"):
            return {}
        payload = await self._rpc("resolve", {"urls": [uri]})
        return {"result": payload, **(candidate.metadata or {})}

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        meta = candidate.metadata or {}
        uri = str(meta.get("uri") or "")
        if not uri.startswith("lbry://"):
            return None
        size = int(meta.get("size_bytes") or 0)
        if size > MAX_DOWNLOAD_BYTES:
            return None
        result = await self._rpc("get", {"uri": uri, "save_file": False})
        url = str(result.get("streaming_url") or "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "player.odycdn.com":
            return None
        suffix = parsed.path.lower().rsplit(".", 1)[-1]
        if suffix not in {"mp4", "webm"}:
            return None
        return MediaRef(url, "video/mp4" if suffix == "mp4" else "video/webm")
