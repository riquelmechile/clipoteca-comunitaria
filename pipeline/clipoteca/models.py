from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AspectRatio = Literal["9:16", "16:9", "1:1", "other"]
Orientation = Literal["vertical", "horizontal", "square", "other"]
ClipStatus = Literal["published", "retired"]
JobStatus = Literal[
    "queued",
    "searching",
    "fetching",
    "processing",
    "publishing",
    "published",
    "no_results",
    "duplicate",
    "failed",
    "rate_limited",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class LocalizedText:
    es: str
    en: str


@dataclass(frozen=True, slots=True)
class LocalizedTags:
    es: list[str]
    en: list[str]


@dataclass(frozen=True, slots=True)
class Source:
    provider: str
    provider_id: str
    source_language: str
    source_title: str
    source_url: str


@dataclass(frozen=True, slots=True)
class Media:
    duration_seconds: float
    width: int
    height: int
    fps: float
    aspect_ratio: AspectRatio
    orientation: Orientation
    codec: str
    container: str
    has_audio: bool
    size_bytes: int
    sha256: str
    perceptual_hash: str


@dataclass(frozen=True, slots=True)
class Storage:
    provider: Literal["github_releases"]
    asset_url: str
    thumbnail_url: str


@dataclass(frozen=True, slots=True)
class Retirement:
    reason: str
    date: str


@dataclass(frozen=True, slots=True)
class Clip:
    id: str
    title: LocalizedText
    description: LocalizedText
    tags: LocalizedTags
    source: Source
    media: Media
    storage: Storage
    status: ClipStatus
    created_at: str
    retirement: Retirement | None = None


@dataclass(frozen=True, slots=True)
class Job:
    request_id: str
    status: JobStatus
    language_counts: dict[str, int]
    provider_counts: dict[str, int]
    source_errors: dict[str, str]
