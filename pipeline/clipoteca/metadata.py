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
    value = value.strip()
    if re.match(r"^(?:https?://|/|[A-Za-z]:[/\\\\])", value, re.IGNORECASE) or re.search(
        r"/[^/]+\.(?:mp4|webm|ogv|mov|mkv)$", value, re.IGNORECASE
    ):
        value = value.rsplit("/", 1)[-1]
    value = re.sub(r"[-_ ]+\d{5,}$", "", value)
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value or not re.search(r"[^\W\d_]", value, flags=re.UNICODE):
        raise MetadataTranslationError("source title has no descriptive words")
    return value[0].upper() + value[1:]


STOPWORDS = {
    "the",
    "and",
    "a",
    "an",
    "in",
    "of",
    "to",
    "on",
    "at",
    "y",
    "en",
    "un",
    "al",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "over",
    "del",
    "los",
    "las",
    "una",
    "unos",
    "unas",
    "para",
    "por",
    "con",
    "que",
    "the",
    "are",
    "dans",
    "sobre",
    "entre",
    "como",
    "de",
    "el",
    "la",
}


def _title_tags(title: str) -> list[str]:
    result = []
    seen = set()
    for word in re.findall(r"[^\W_]+", title, flags=re.UNICODE):
        key = word.casefold()
        if len(word) > 2 and key not in STOPWORDS and key not in seen:
            seen.add(key)
            result.append(word)
    return result[:12]


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
    description_language = (
        source_data.get("description_language") if isinstance(source_data, dict) else None
    )
    if description_language not in {"es", "en", "zh", "ru"}:
        description_language = candidate.source_language
    desc = _bilingual(raw_description, description_language, translator)
    # Only provider-owned text is searchable. Discovery terms are not metadata.
    tags = {}
    provider_tags = []
    if isinstance(source_data, dict):
        for key in ("tags", "keywords"):
            value = source_data.get(key)
            if isinstance(value, str):
                provider_tags.append(value)
            elif isinstance(value, list):
                provider_tags.extend(item for item in value if isinstance(item, str))
    extra = {"es": [], "en": []}
    for value in provider_tags:
        if not value.strip():
            continue
        try:
            translated = _bilingual(value, candidate.source_language, translator)
        except MetadataTranslationError:
            # Optional provider tags never invalidate factual title/description.
            if candidate.source_language in extra:
                extra[candidate.source_language].extend(_title_tags(value))
            continue
        for language in extra:
            extra[language].extend(_title_tags(translated[language]))
    for language in ("es", "en"):
        words = [
            *_title_tags(title[language]),
            *_title_tags(desc[language]),
            *extra[language],
        ]
        seen = set()
        tags[language] = []
        for word in words:
            if word.casefold() not in seen:
                seen.add(word.casefold())
                tags[language].append(word)
    return LocalizedMetadata(
        title=title,
        description=desc,
        tags=tags,
        original_title=original,
        original_language=candidate.source_language,
    )
