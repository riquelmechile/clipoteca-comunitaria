"""Provider contract for discovery and separate media-resolution phases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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


class Adapter(ABC):
    name: str
    category: str = "video"
    accepts_text_languages: tuple[str, ...] = ("es", "en", "zh", "ru")
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
