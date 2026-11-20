from __future__ import annotations

import hashlib
import json

import pytest
from clipoteca.translate.argos import TranslationFailed, translate_pair
from clipoteca.translate.dictionary import CONCEPTS, find_concept
from clipoteca.translate.expand import detect_language, expand_query


def test_curated_dictionary_size_and_primary_exact():
    assert len(CONCEPTS) >= 150
    assert find_concept("rain") is find_concept("lluvia")
    expansion = expand_query("lluvia")
    assert expansion.terms == {
        "es": "lluvia",
        "en": "rain",
        "zh": "下雨",
        "ru": "дождь",
    }
    assert expansion.expanded["en"] == ["heavy rain"]


@pytest.mark.parametrize("query,lang", [("下雨", "zh"), ("дождь", "ru"), ("rain", "en")])
def test_detect_script_and_reverse_dictionary(query, lang):
    assert detect_language(query) == lang
    assert expand_query(query).terms["es"] == "lluvia"
    assert expand_query(query).terms["en"] == "rain"


def test_missing_argos_reports_errors_without_invented_words(monkeypatch):
    monkeypatch.delenv("ARGOS_MODEL_PINS_JSON", raising=False)
    result = expand_query("maravilloso paisaje espacial")
    assert result.terms == {"es": "maravilloso paisaje espacial"}
    assert set(result.errors) == {"en", "zh", "ru"}
    assert all("translation_failed" in item for item in result.errors.values())


def test_argos_checksum_guard(monkeypatch, tmp_path):
    pkg = tmp_path / "translate-es_en-1_9.argosmodel"
    pkg.write_bytes(b"tampered")
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "es_en": {
                    "version": "1.9",
                    "sha256": hashlib.sha256(b"expected").hexdigest(),
                }
            }
        )
    )
    monkeypatch.setenv("ARGOS_MODEL_PINS_JSON", str(manifest))
    monkeypatch.setenv("ARGOS_MODEL_CACHE_DIR", str(tmp_path))
    with pytest.raises(TranslationFailed, match="package_sha256_mismatch"):
        translate_pair("hola", "es", "en")


def test_unknown_query_expands_using_injected_pivot():
    table = {
        ("es", "en"): "new concept",
        ("en", "zh"): "新概念",
        ("en", "ru"): "новая концепция",
    }
    result = expand_query("concepto nuevo", translator=lambda _, src, dst: table[src, dst])
    assert result.terms == {
        "es": "concepto nuevo",
        "en": "new concept",
        "zh": "新概念",
        "ru": "новая концепция",
    }


def test_translator_failure_is_localized_per_language():
    def broken(term, src, dst):
        if dst == "ru":
            raise RuntimeError("offline model corrupt")
        return {"en": "unknown", "zh": "未知"}[dst]

    result = expand_query("término desconocido", translator=broken)
    assert "ru" not in result.terms
    assert "translation_failed" in result.errors["ru"]
    assert result.terms["zh"] == "未知"
