"""Provider contract for discovery and separate media-resolution phases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


def detect_source_language(text: str) -> str:
    """Detect language of the source title rather than the query."""
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    if re.search(r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00fc\u00bf\u00a1]", text.casefold()):
        return "es"
    if re.search(r"\b(el|la|los|las|una|unas|lluvia|calle|cielo|noche|paisaje)\b", text.casefold()):
        return "es"
    return "en"


class SupportStatus(StrEnum):
    SUPPORTED_SEARCH_DOWNLOAD = "SUPPORTED_SEARCH_DOWNLOAD"
    SUPPORTED_SEARCH_ONLY = "SUPPORTED_SEARCH_ONLY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


class ProviderError(RuntimeError):
    status = SupportStatus.UNAVAILABLE


class RateLimited(ProviderError):
    status = SupportStatus.RATE_LIMITED


class ProviderTimeout(ProviderError):
    status = SupportStatus.TIMEOUT


class AuthRequired(ProviderError):
    status = SupportStatus.AUTH_REQUIRED


@dataclass(frozen=True)
class SearchLimits:
    max_pages: int = 2
    max_candidates: int = 25
    timeout_seconds: float = 6.0


@dataclass(frozen=True)
class Candidate:
    provider: str
    provider_id: str
    term: str
    language: str
    source_url: str
    source_title: str
    source_language: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] | None = None

    @property
    def origin_id(self) -> str:
        return f"{self.provider}:{self.provider_id}"


@dataclass(frozen=True)
class MediaRef:
    url: str
    mime: str
    size_bytes: int | None = None
    referer: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    audio_url: str | None = None
    # Some signed CDN URLs bind the browser family that loaded the page
    # (OK/VK CDN for Dzen carries srcAg=CHROME and answers 400 otherwise).
    user_agent: str | None = None


class Adapter(ABC):
    name: str
    category: str = "video"
    accepts_text_languages: tuple[str, ...] = ("es", "en", "zh", "ru")
    discovery_priority: int = 0
    rate_limit: int | None = None
    auth: str = "none"
    support_status: SupportStatus = SupportStatus.SUPPORTED_SEARCH_ONLY

    @abstractmethod
    async def search(self, term: str, lang: str, limits: SearchLimits) -> list[Candidate]:
        """Discover candidates, no content downloads."""

    @abstractmethod
    async def metadata(self, candidate: Candidate) -> dict[str, Any]:
        """Resolve provider metadata from its identifier."""

    @abstractmethod
    async def direct_media(self, candidate: Candidate) -> MediaRef | None:
        """Return a real direct media URL, never an HTML page."""

    def origin_id(self, candidate: Candidate) -> str:
        return candidate.origin_id
