"""Preserve factual source provenance and produce ES/EN metadata without invention."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from .providers.base import Candidate
from .translate.argos import TranslationFailed, translate_text
from .translate.dictionary import find_concept, normalize


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


def _keyword_tags(
    text: str,
    source: str,
    target: str,
    translator: Callable[[str, str, str], str] | None,
) -> list[str]:
    """Translate query tags per word, curated dictionary before vetted Argos."""
    result = []
    for word in _title_tags(text):
        try:
            concept = find_concept(word)
            if source == target:
                translated = word
            elif (
                concept is not None
                and source in concept.terms
                and target in concept.terms
                and normalize(concept.terms[source]) == normalize(word)
            ):
                translated = concept.terms[target]
            else:
                translated = translate_text(word, source, target, translator=translator)
        except TranslationFailed as exc:
            raise MetadataTranslationError(str(exc)) from exc
        result.extend(_title_tags(translated))
    return result


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
    # Translate query tags separately, not as sentences. Never retain
    # hallucinated repeated fragments or language stopwords in the index.
    tags = {}
    for language in ("es", "en"):
        words = [
            *_title_tags(title[language]),
            *_keyword_tags(candidate.term, candidate.language, language, translator),
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
