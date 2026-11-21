"""The 15-source contract used by CLI, health checks and future MCP list_sources.

Status is a capability/credential snapshot, never a promise that a query
will find downloadable videos. Environment variable VALUES are never exposed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .providers.bilibili import Bilibili
from .providers.douyin import Douyin
from .providers.instagram import Instagram
from .providers.internet_archive import InternetArchive
from .providers.nasa import NASA
from .providers.odysee import Odysee
from .providers.peertube import PeerTube
from .providers.pexels import Pexels
from .providers.pixabay import Pixabay
from .providers.rutube import Rutube
from .providers.snapchat import Snapchat
from .providers.tiktok import TikTok
from .providers.vk_video import VKVideo
from .providers.wikimedia_commons import WikimediaCommons
from .providers.youtube import YouTube
from .providers.base import SupportStatus


@dataclass(frozen=True)
class SourceSpec:
    name: str
    adapter: type
    requires: tuple[str, ...]
    scope: str
    notes: str


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        "pexels",
        Pexels,
        ("PEXELS_API_KEY",),
        "catalog_search",
        "key required; direct MP4 when present",
    ),
    SourceSpec(
        "pixabay",
        Pixabay,
        ("PIXABAY_API_KEY",),
        "catalog_search",
        "key required; video variants when present",
    ),
    SourceSpec(
        "wikimedia_commons",
        WikimediaCommons,
        (),
        "public_commons",
        "file namespace, inspect rights",
    ),
    SourceSpec(
        "internet_archive", InternetArchive, (), "public_archive", "movie catalog; rights vary"
    ),
    SourceSpec(
        "nasa", NASA, (), "public_archive", "asset manifest; source title from NASA metadata"
    ),
    SourceSpec(
        "youtube",
        YouTube,
        ("YOUTUBE_API_KEY",),
        "global_data_api",
        "Data API has no direct MP4 endpoint",
    ),
    SourceSpec(
        "rutube",
        Rutube,
        (),
        "public_search",
        "HLS download path implemented; live M12/M16 evidence still pending",
    ),
    SourceSpec(
        "peertube",
        PeerTube,
        (),
        "trusted_federation",
        "allowed instances only; bounded direct MP4/WebM",
    ),
    SourceSpec(
        "odysee", Odysee, (), "public_search", "SDK claim resolution, size and rights gates"
    ),
    SourceSpec(
        "bilibili",
        Bilibili,
        (),
        "public_search",
        "risk control/WBI may block search; media not reusable",
    ),
    SourceSpec(
        "vk_video",
        VKVideo,
        ("VK_ACCESS_TOKEN",),
        "oauth_search",
        "third-party copying requires separate rights",
    ),
    SourceSpec(
        "douyin",
        Douyin,
        ("DOUYIN_ACCESS_TOKEN",),
        "authorized_account_only",
        "no general global video search",
    ),
    SourceSpec(
        "tiktok",
        TikTok,
        ("TIKTOK_ACCESS_TOKEN",),
        "authorized_account_only",
        "Display API, no global search",
    ),
    SourceSpec(
        "instagram",
        Instagram,
        ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID"),
        "authorized_professional_account_only",
        "no general global video search",
    ),
    SourceSpec(
        "snapchat",
        Snapchat,
        ("SNAPCHAT_ACCESS_TOKEN", "SNAPCHAT_PROFILE_ID"),
        "authorized_profile_only",
        "profile Spotlight list, no global video search",
    ),
)


def list_sources(*, env: dict[str, str] | None = None) -> list[dict]:
    """Return source capability/status metadata without credentials or remote I/O."""
    provided = os.environ if env is None else env
    rows = []
    for spec in SOURCES:
        missing = [key for key in spec.requires if not provided.get(key)]
        status = SupportStatus.AUTH_REQUIRED.value if missing else spec.adapter.support_status.value
        rows.append(
            {
                "name": spec.name,
                "category": spec.adapter.category,
                "status": status,
                "support_status": spec.adapter.support_status.value,
                "accepts_text_languages": list(spec.adapter.accepts_text_languages),
                "languages_semantically_verified": False,
                "scope": spec.scope,
                "requires": list(spec.requires),
                "missing_credential_names": missing,
                "download_supported": not missing
                and (spec.adapter.support_status == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD),
                "notes": spec.notes,
            }
        )
    return rows
