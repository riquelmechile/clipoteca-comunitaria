"""Shared, conservative parsing helpers for institutional archive APIs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from .base import MediaRef

VIDEO_EXTENSIONS = (".mp4", ".webm", ".ogv", ".mov", ".m4v")


def first_text(value: Any, default: str = "") -> str:
    """Return the first useful display string from common API field shapes."""
    if isinstance(value, str):
        return value.strip() or default
    if isinstance(value, list):
        for item in value:
            text = first_text(item)
            if text:
                return text
    if isinstance(value, dict):
        for key in ("content", "value", "label", "title"):
            text = first_text(value.get(key))
            if text:
                return text
    return default


def is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def is_direct_video_url(value: Any, mime: Any = "") -> bool:
    if not is_https_url(value):
        return False
    if isinstance(mime, str) and mime.casefold().startswith("video/"):
        return True
    path = urlsplit(value).path.casefold()
    return path.endswith(VIDEO_EXTENSIONS)


def video_mime(url: str, declared: Any = "") -> str:
    if isinstance(declared, str) and declared.casefold().startswith("video/"):
        return declared.casefold().split(";", 1)[0]
    path = urlsplit(url).path.casefold()
    if path.endswith(".webm"):
        return "video/webm"
    if path.endswith(".ogv"):
        return "video/ogg"
    if path.endswith(".mov"):
        return "video/quicktime"
    return "video/mp4"


def media_ref(url: Any, mime: Any = "", size: Any = None) -> MediaRef | None:
    if not is_direct_video_url(url, mime):
        return None
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None
    return MediaRef(str(url), video_mime(str(url), mime), size_bytes)


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    """Yield nested objects without interpreting unrelated strings as media."""
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_dicts(nested)


def nested_direct_media(value: Any) -> MediaRef | None:
    """Find an explicitly described direct video resource in a provider row."""
    if isinstance(value, str):
        return media_ref(value)
    if isinstance(value, list):
        for item in value:
            ref = nested_direct_media(item)
            if ref:
                return ref
        return None
    for row in walk_dicts(value):
        mime = row.get("mimeType") or row.get("mime") or row.get("type") or row.get("format")
        for key in ("downloadUrl", "fileUrl", "contentUrl", "url", "href"):
            ref = media_ref(row.get(key), mime, row.get("size") or row.get("sizeBytes"))
            if ref:
                return ref
    return None
