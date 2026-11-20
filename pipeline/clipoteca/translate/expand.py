"""Four-language mandatory expansion. Never fabricate a missing translation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .argos import TranslationFailed, translate_text
from .dictionary import LANGUAGES, SYNONYMS, find_concept, normalize


@dataclass(frozen=True)
class QueryExpansion:
    concept: str
    terms: dict[str, str]
    expanded: dict[str, list[str]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return all(self.terms.get(language) for language in LANGUAGES)


def detect_language(query: str) -> str:
    if re.search(r"[\u3400-\u9fff]", query):
        return "zh"
    if re.search(r"[\u0400-\u04ff]", query):
        return "ru"
    concept = find_concept(query)
    if concept:
        for language in ("es", "en"):
            if normalize(concept.terms[language]) == normalize(query):
                return language
    return "es"


def expand_query(
    query: str,
    *,
    translator: Callable[[str, str, str], str] | None = None,
) -> QueryExpansion:
    concept = find_concept(query)
    lang = detect_language(query)
    normal = normalize(query)
    if not normal:
        raise ValueError("query cannot be empty")
    if concept:
        primary = dict(concept.terms)
        synonyms = SYNONYMS.get(normal, {})
        return QueryExpansion(
            concept=primary["es"],
            terms=primary,
            expanded={language: list(synonyms.get(language, [])) for language in LANGUAGES},
        )
    terms: dict[str, str] = {lang: query.strip()}
    errors: dict[str, str] = {}
    for target in LANGUAGES:
        if target == lang:
            continue
        try:
            terms[target] = translate_text(query, lang, target, translator=translator)
        except TranslationFailed as exc:
            errors[target] = str(exc)
    return QueryExpansion(concept=query.strip(), terms=terms, errors=errors)
