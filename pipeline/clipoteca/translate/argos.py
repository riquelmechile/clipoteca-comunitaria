"""Argos Translate adapter with strictly verified, cached model artifacts.

No unpinned download or model installation occurs. Supply the exact package
SHA256 for each source/target pair in ARGOS_MODEL_PINS_JSON (JSON mapping).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

PAIRS = {
    ("es", "en"): ("translate-es_en", "1.9"),
    ("en", "es"): ("translate-en_es", "1.0"),
    ("en", "zh"): ("translate-en_zh", "1.9"),
    ("zh", "en"): ("translate-zh_en", "1.9"),
    ("en", "ru"): ("translate-en_ru", "1.9"),
    ("ru", "en"): ("translate-ru_en", "1.9"),
}


class TranslationFailed(RuntimeError):
    def __init__(self, src: str, dst: str, reason: str) -> None:
        self.src, self.dst, self.reason = src, dst, reason
        super().__init__(f"translation_failed:{src}->{dst}:{reason}")


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
        if not value or not value.strip():
            raise TranslationFailed(src, dst, "empty_translation")
        return value.strip()

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
    if not result or not result.strip():
        raise TranslationFailed(src, dst, "empty_translation")
    return result.strip()


def translate_text(
    term: str, src: str, dst: str, *, translator: Callable[[str, str, str], str] | None = None
) -> str:
    if src == dst:
        return term
    english = term if src == "en" else translate_pair(term, src, "en", translator=translator)
    return english if dst == "en" else translate_pair(english, "en", dst, translator=translator)
