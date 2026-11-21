"""Snapchat Public Profile API: authenticated Spotlights by profile ID.

Global public video search is not available through this endpoint. Media URLs
may be temporary and are intentionally not downloaded automatically.
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
from .http import fetch_json, provider_client

API = "https://businessapi.snapchat.com/v1/public_profiles"
PROFILE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


class Snapchat(Adapter):
    name = "snapchat"
    category = "social_video"
    auth = "SNAPCHAT_ACCESS_TOKEN + SNAPCHAT_PROFILE_ID"
    support_status = SupportStatus.SUPPORTED_SEARCH_ONLY
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(
        self,
        *,
        token: str | None = None,
        profile_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.token = token if token is not None else os.getenv("SNAPCHAT_ACCESS_TOKEN", "")
        self.profile_id = (
            profile_id if profile_id is not None else os.getenv("SNAPCHAT_PROFILE_ID", "")
        )
        self.client = client or provider_client()

    async def _spotlights(self, limit: int) -> list[dict]:
        if not self.token or not PROFILE.fullmatch(self.profile_id):
            raise AuthRequired("Snapchat authorized public profile required")
        data = await fetch_json(
            self.client,
            f"{API}/{self.profile_id}/spotlights",
            params={"limit": min(100, limit)},
            headers={"Authorization": "Bearer " + self.token},
        )
        if data.get("request_status") not in (None, "SUCCESS", "PARTIAL"):
            raise ProviderError("Snapchat profile request rejected")
        return [
            entry["spotlight"]
            for entry in (data.get("spotlights") or [])
            if isinstance(entry, dict)
            and entry.get("sub_request_status") == "SUCCESS"
            and isinstance(entry.get("spotlight"), dict)
        ]

    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        # Only this authorized profile, no global Spotlight discovery.
        rows = await self._spotlights(limits.max_candidates)
        found = []
        for row in rows:
            vid = str(row.get("id") or "")
            if not vid or row.get("status") not in (None, "LIVE"):
                continue
            title = str(row.get("description") or vid)
            if term and term.casefold() not in title.casefold():
                continue
            found.append(
                Candidate(
                    provider=self.name,
                    provider_id=vid,
                    term=term,
                    language=lang,
                    source_url="https://www.snapchat.com/spotlight",
                    source_title=title,
                    source_language=detect_source_language(title),
                    metadata={
                        "scope": "authorized_profile_only",
                        "profile_id": self.profile_id,
                        "media_url": row.get("media_url"),
                        "created_at": row.get("created_at"),
                    },
                )
            )
        return found

    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        return dict(candidate.metadata)

    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        return None
