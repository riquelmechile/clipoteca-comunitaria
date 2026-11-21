"""Preserve factual source provenance and produce ES/EN metadata without invention."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from .providers.base import Candidate
from .translate.argos import TranslationFailed, translate_text


class MetadataTranslationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalizedMetadata:
    title: dict[str, str]
    description: dict[str, str]
    tags: dict[str, list[str]]
    original_title: str
    original_language: str


def _bilingual(
    text: str,
    source: str,
    translator: Callable[[str, str, str], str] | None = None,
) -> dict[str, str]:
    if source not in {"es", "en", "zh", "ru"}:
        raise MetadataTranslationError("unsupported_source_language")
    result = {}
    try:
        for target in ("es", "en"):
            result[target] = (
                text
                if source == target
                else translate_text(text, source, target, translator=translator)
            )
    except TranslationFailed as exc:
        raise MetadataTranslationError(str(exc)) from exc
    if not result["es"].strip() or not result["en"].strip():
        raise MetadataTranslationError("missing_bilingual_metadata")
    return result


def humanize_source_title(value: str) -> str:
    """Remove stock-video URL slugs/IDs without manufacturing scene details."""
    value = value.strip().rsplit("/", 1)[-1]
    value = re.sub(r"[-_ ]+\d{5,}$", "", value)
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value or not re.search(r"[^\W\d_]", value, flags=re.UNICODE):
        raise MetadataTranslationError("source title has no descriptive words")
    return value[0].upper() + value[1:]


def _title_tags(title: str) -> list[str]:
    return [word for word in re.findall(r"[^\W_]+", title, flags=re.UNICODE) if len(word) > 2][:12]


def localize_candidate(
    candidate: Candidate,
    *,
    translator: Callable[[str, str, str], str] | None = None,
) -> LocalizedMetadata:
    original = candidate.source_title.strip()
    if not original:
        raise MetadataTranslationError("source title missing")
    readable = humanize_source_title(original)
    metadata = candidate.metadata or {}
    source_data = metadata.get("data", metadata)
    description = source_data.get("description") if isinstance(source_data, dict) else None
    raw_description = (
        description.strip() if isinstance(description, str) and description.strip() else readable
    )
    title = _bilingual(readable, candidate.source_language, translator)
    desc = _bilingual(raw_description, candidate.source_language, translator)
    # Search term is factual; when not in curated dictionary, translate or fail.
    keyword = _bilingual(candidate.term, candidate.language, translator)
    return LocalizedMetadata(
        title=title,
        description=desc,
        tags={
            language: list(dict.fromkeys([keyword[language], *_title_tags(title[language])]))
            for language in ("es", "en")
        },
        original_title=original,
        original_language=candidate.source_language,
    )
