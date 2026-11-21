"""The source contract used by CLI, health checks and MCP list_sources.

Status is a capability/credential snapshot, never a promise that a query
will find downloadable videos. Environment variable VALUES are never exposed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .providers.bilibili import Bilibili
from .providers.coverr import Coverr
from .providers.dpla import DPLA
from .providers.europeana import Europeana
from .providers.library_of_congress import LibraryOfCongress
from .providers.nara import NARA
from .providers.smithsonian import Smithsonian
from .providers.douyin import Douyin
from .providers.dzen import Dzen
from .providers.instagram import Instagram
from .providers.internet_archive import InternetArchive
from .providers.nasa import NASA
from .providers.odysee import Odysee
from .providers.ok_ru import OkRu
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
        "library_of_congress",
        LibraryOfCongress,
        (),
        "public_archive",
        "public catalog; restricted downloads are excluded",
    ),
    SourceSpec(
        "coverr",
        Coverr,
        ("COVERR_API_KEY",),
        "catalog_search",
        "key required; download MP4 preferred over preview",
    ),
    SourceSpec(
        "europeana",
        Europeana,
        ("EUROPEANA_API_KEY",),
        "cultural_archive",
        "key required; direct video media only",
    ),
    SourceSpec(
        "nara",
        NARA,
        ("NARA_API_KEY",),
        "government_archive",
        "key required; explicit direct video resources only",
    ),
    SourceSpec(
        "dpla",
        DPLA,
        ("DPLA_API_KEY",),
        "cultural_archive",
        "key required; moving-image search, usually metadata only",
    ),
    SourceSpec(
        "smithsonian",
        Smithsonian,
        ("SMITHSONIAN_API_KEY",),
        "cultural_archive",
        "key required; video search, usually metadata only",
    ),
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
        "bounded HLS download; live CDN evidence from M12 still pending",
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
        "ok_ru",
        OkRu,
        (),
        "public_search",
        "anonymous HTML discovery; CDN download remains best-effort pending live smoke",
    ),
    SourceSpec(
        "vk_video",
        VKVideo,
        (),
        "public_search",
        "anonymous Web2 token; VK_ACCESS_TOKEN remains optional",
    ),
    SourceSpec(
        "dzen",
        Dzen,
        (),
        "public_search",
        "cookie-aware public search; stream availability is best-effort",
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


# Single authoritative declarative capability map. These are adapter claims,
# never evidence of a successful live request. M12 candidate probes measure that.
CAPABILITY_LEVELS = frozenset({"supported", "best_effort", "unavailable"})
SOURCE_CAPABILITIES: dict[str, dict[str, str]] = {
    "library_of_congress": {
        "search": "best_effort",
        "preview": "best_effort",
        "download": "best_effort",
    },
    "coverr": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "europeana": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "nara": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "dpla": {"search": "best_effort", "preview": "unavailable", "download": "unavailable"},
    "smithsonian": {"search": "best_effort", "preview": "unavailable", "download": "unavailable"},
    "pexels": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "pixabay": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "wikimedia_commons": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "internet_archive": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "nasa": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "youtube": {"search": "supported", "preview": "best_effort", "download": "unavailable"},
    "rutube": {"search": "supported", "preview": "supported", "download": "best_effort"},
    "peertube": {"search": "supported", "preview": "supported", "download": "supported"},
    "odysee": {"search": "supported", "preview": "best_effort", "download": "supported"},
    "bilibili": {"search": "best_effort", "preview": "best_effort", "download": "unavailable"},
    "ok_ru": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "vk_video": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "dzen": {"search": "best_effort", "preview": "best_effort", "download": "best_effort"},
    "douyin": {"search": "best_effort", "preview": "best_effort", "download": "unavailable"},
    "tiktok": {"search": "best_effort", "preview": "best_effort", "download": "unavailable"},
    "instagram": {"search": "best_effort", "preview": "best_effort", "download": "unavailable"},
    "snapchat": {"search": "best_effort", "preview": "best_effort", "download": "unavailable"},
}


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
                "capabilities": dict(SOURCE_CAPABILITIES[spec.name]),
                "requires": list(spec.requires),
                "missing_credential_names": missing,
                "download_supported": not missing
                and (spec.adapter.support_status == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD),
                "notes": spec.notes,
            }
        )
    return rows
