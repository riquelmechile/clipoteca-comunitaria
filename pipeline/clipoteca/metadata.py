"""Preserve factual source provenance and produce ES/EN metadata without invention."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .providers.base import Candidate
from .translate.argos import TranslationFailed, translate_text
from .translate.dictionary import find_concept


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
    known = find_concept(text)
    if known:
        return {"es": known.terms["es"], "en": known.terms["en"]}
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


def localize_candidate(
    candidate: Candidate,
    *,
    translator: Callable[[str, str, str], str] | None = None,
) -> LocalizedMetadata:
    original = candidate.source_title.strip()
    if not original:
        raise MetadataTranslationError("source title missing")
    metadata = candidate.metadata or {}
    source_data = metadata.get("data", metadata)
    description = source_data.get("description") if isinstance(source_data, dict) else None
    raw_description = (
        description.strip() if isinstance(description, str) and description.strip() else original
    )
    title = _bilingual(original, candidate.source_language, translator)
    desc = _bilingual(raw_description, candidate.source_language, translator)
    # Search term is factual; when not in curated dictionary, translate or fail.
    keyword = _bilingual(candidate.term, candidate.language, translator)
    return LocalizedMetadata(
        title=title,
        description=desc,
        tags={"es": [keyword["es"]], "en": [keyword["en"]]},
        original_title=original,
        original_language=candidate.source_language,
    )
