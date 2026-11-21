"""Argos Translate adapter with strictly verified, cached model artifacts.

No unpinned download or model installation occurs. Supply the exact package
SHA256 for each source/target pair in ARGOS_MODEL_PINS_JSON (JSON mapping).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable

from .dictionary import find_concept, normalize

PAIRS = {
    ("es", "en"): ("translate-es_en", "1.9"),
    ("en", "es"): ("translate-en_es", "1.0"),
    ("en", "zh"): ("translate-en_zh", "1.9"),
    ("zh", "en"): ("translate-zh_en", "1.9"),
    ("en", "ru"): ("translate-en_ru", "1.9"),
    ("ru", "en"): ("translate-ru_en", "1.9"),
}


PUBLIC_TRANSLATION_REASONS = frozenset(
    {
        "unsupported_language_pair",
        "model_pins_not_configured",
        "invalid_model_pin",
        "model_pin_mismatch",
        "package_missing",
        "package_sha256_mismatch",
        "argos_not_installed",
        "argos_runtime_error",
        "translator_error",
        "empty_translation",
        "disproportionate_translation",
        "repeated_translation",
        "unchanged_translation",
    }
)
_PUBLIC_TRANSLATION_PATTERN = re.compile(
    r"translation_failed:(es|en|zh|ru)->(es|en|zh|ru):([a-z_]+)"
)


def public_translation_error(message: str) -> str | None:
    """Extract only an allowlisted machine error, never upstream exception text."""
    for match in _PUBLIC_TRANSLATION_PATTERN.finditer(message):
        if match.group(3) in PUBLIC_TRANSLATION_REASONS:
            return match.group(0)
    return None


class TranslationFailed(RuntimeError):
    def __init__(self, src: str, dst: str, reason: str) -> None:
        self.src, self.dst, self.reason = src, dst, reason
        super().__init__(f"translation_failed:{src}->{dst}:{reason}")


def _validate_translation(term: str, value: str, src: str, dst: str) -> str:
    """Reject model corruption instead of indexing invented or repeated text."""
    result = value.strip()
    if not result:
        raise TranslationFailed(src, dst, "empty_translation")
    # Compare Unicode words and alphanumeric characters, not raw punctuation.
    # Short bilingual notes and place names can expand significantly in Argos.
    def size(text: str) -> tuple[int, int]:
        words = re.findall(r"[^\\W_]+(?:['’\\-][^\\W_]+)*", text, flags=re.UNICODE)
        return sum(len(word) for word in words), len(words)

    source_chars, source_words = size(term)
    result_chars, result_words = size(result)
    if (
        result_chars > max(480, 14 * max(source_chars, 1))
        or (
            result_chars > max(240, 6 * source_chars + 100)
            and result_words > max(36, 6 * source_words + 12)
        )
    ):
        raise TranslationFailed(src, dst, "disproportionate_translation")
    if re.search(r"(.{3,24}?)\1{3,}", result, flags=re.IGNORECASE):
        raise TranslationFailed(src, dst, "repeated_translation")
    # Exact matches are valid for numbers, acronyms and curated loanwords
    # (e.g. taxi), but not for normal words which should be translated.
    if normalize(result) == normalize(term) and re.search(r"[^\W\d_]{3,}", term):
        concept = find_concept(term)
        loanword = (
            concept is not None
            and src in concept.terms
            and dst in concept.terms
            and normalize(concept.terms[src]) == normalize(concept.terms[dst])
        )
        if not loanword and (term != term.upper() or len(term) < 3):
            raise TranslationFailed(src, dst, "unchanged_translation")
    return result


def _package_spec(src: str, dst: str) -> tuple[Path, str]:
    pair = PAIRS.get((src, dst))
    if pair is None:
        raise TranslationFailed(src, dst, "unsupported_language_pair")
    filename = pair[0] + "-" + pair[1].replace(".", "_") + ".argosmodel"
    manifest = os.environ.get("ARGOS_MODEL_PINS_JSON")
    if not manifest:
        raise TranslationFailed(src, dst, "model_pins_not_configured")
    try:
        checksums = json.loads(Path(manifest).read_text(encoding="utf-8"))
        checksum = checksums[f"{src}_{dst}"]["sha256"]
        expected_version = checksums[f"{src}_{dst}"]["version"]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise TranslationFailed(src, dst, "invalid_model_pin") from exc
    if expected_version != pair[1] or len(checksum) != 64:
        raise TranslationFailed(src, dst, "model_pin_mismatch")
    folder = Path(os.environ.get("ARGOS_MODEL_CACHE_DIR", ".cache/argos"))
    return folder / filename, checksum.lower()


def _verify_package(path: Path, checksum: str, src: str, dst: str) -> None:
    if not path.is_file():
        raise TranslationFailed(src, dst, "package_missing")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != checksum:
        raise TranslationFailed(src, dst, "package_sha256_mismatch")


def translate_pair(
    term: str, src: str, dst: str, *, translator: Callable[[str, str, str], str] | None = None
) -> str:
    if src == dst:
        return term
    if translator is not None:
        try:
            value = translator(term, src, dst)
        except Exception as exc:
            raise TranslationFailed(src, dst, "translator_error") from exc
        return _validate_translation(term, value, src, dst)

    path, checksum = _package_spec(src, dst)
    _verify_package(path, checksum, src, dst)
    try:
        from argostranslate import package, translate  # type: ignore[import-untyped]
    except ImportError as exc:
        raise TranslationFailed(src, dst, "argos_not_installed") from exc
    try:
        installed = package.get_installed_packages()
        if not any(p.from_code == src and p.to_code == dst for p in installed):
            package.install_from_path(path)
        result: str = translate.translate(term, src, dst)
    except Exception as exc:
        raise TranslationFailed(src, dst, "argos_runtime_error") from exc
    return _validate_translation(term, result, src, dst)


def translate_text(
    term: str, src: str, dst: str, *, translator: Callable[[str, str, str], str] | None = None
) -> str:
    if src == dst:
        return term
    # Never bypass an unavailable verified model just because a term exists
    # in the dictionary. A corrupt model response may use curated fallback.
    try:
        english = term if src == "en" else translate_pair(term, src, "en", translator=translator)
        return english if dst == "en" else translate_pair(english, "en", dst, translator=translator)
    except TranslationFailed as exc:
        if exc.reason not in {
            "unchanged_translation",
            "repeated_translation",
            "disproportionate_translation",
        }:
            raise
        concept = find_concept(term)
        if (
            concept is not None
            and src in concept.terms
            and dst in concept.terms
            and normalize(concept.terms[src]) == normalize(term)
        ):
            translated = concept.terms[dst]
            return (
                translated.capitalize() if term[:1].isupper() and not term.isupper() else translated
            )
        raise
