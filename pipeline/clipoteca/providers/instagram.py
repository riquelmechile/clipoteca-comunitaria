"""Instagram Platform: scoped professional-account media, not global search.

API with Instagram Login; official Meta media fields and user media edge.
No third-party content ingestion; owner-managed media is search-only until
a separate rights-verification feature is configured.
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
from .http import fetch_json

API = "https://graph.instagram.com/v27.0"


class Instagram(Adapter):
    name = "instagram"
    category = "social_video"
    auth = "INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_USER_ID"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self,
        *,
        token: str | None = None,
        user_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = token if token is not None else os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        self.user_id = user_id if user_id is not None else os.getenv("INSTAGRAM_USER_ID", "")
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def _own_media(self, max_results: int) -> list[dict]:
        if not self.token or not re.fullmatch(r"[0-9]{5,30}", self.user_id):
            raise AuthRequired("Instagram professional account and token required")
        payload = await fetch_json(
            self.client,
            f"{API}/{self.user_id}/media",
            params={
                "fields": "id,media_type,media_url,permalink,caption,timestamp",
                "limit": min(50, max_results),
                "access_token": self.token,
            },
        )
        if "error" in payload:
            err = payload["error"] or {}
            if isinstance(err, dict) and err.get("code") in (10, 190, 200):
                raise AuthRequired("Instagram Graph permissions/token")
            raise ProviderError("Instagram Graph error")
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise ProviderError("Instagram media response malformed")
        return [item for item in data if isinstance(item, dict)]

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        rows = await self._own_media(limits.max_candidates)
        found = []
        for row in rows:
            if row.get("media_type") != "VIDEO":
                continue
            media_id = str(row.get("id") or "")
            if not re.fullmatch(r"[0-9]{5,30}", media_id):
                continue
            title = str(row.get("caption") or media_id)
            if term and term.casefold() not in title.casefold():
                continue
            url = str(row.get("permalink") or "")
            if not url.startswith("https://www.instagram.com/"):
                url = "https://www.instagram.com/"
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=media_id,
                    term=term,
                    language=lang,
                    source_url=url,
                    source_title=title,
                    source_language=detect_source_language(title),
                    metadata={
                        "scope": "authorized_professional_account",
                        "media_type": "VIDEO",
                        "media_url": row.get("media_url"),
                    },
                )
            )
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return dict(candidate.metadata)

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        # Meta media_url does not grant copying or redistributing third-party videos.
        # Explicit opt-in and rights review must be separately implemented.
        return None
