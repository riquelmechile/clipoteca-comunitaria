"""Opt-in real, checksum-pinned Argos regression for Issue #26 and ingest query 'olas'."""

import asyncio
import os
from pathlib import Path

import pytest

from clipoteca.contributions import CommunityContribution, CommunityIssue
from clipoteca.metadata import MetadataTranslationError, localize_candidate
from clipoteca.providers.base import SearchLimits
from clipoteca.translate.argos import TranslationFailed, translate_pair


def test_ordinary_olas_accepts_translation_and_rejects_corruption():
    """Keep the validator contract covered without requiring real model files."""
    assert (
        translate_pair("olas", "es", "en", translator=lambda _term, _src, _dst: "waves") == "waves"
    )
    with pytest.raises(
        TranslationFailed,
        match=r"^translation_failed:es->en:(disproportionate|repeated)_translation$",
    ):
        translate_pair(
            "olas",
            "es",
            "en",
            translator=lambda _term, _src, _dst: "mainstream" * 200,
        )


@pytest.mark.skipif(
    os.getenv("ARGOS_REAL_INTEGRATION") != "1",
    reason="requires preverified real Argos models and sentence resources",
)
def test_real_argos_issue26_and_ordinary_olas():
    assert Path(os.environ["ARGOS_MODEL_PINS_JSON"]).is_file()
    assert Path(os.environ["ARGOS_MODEL_CACHE_DIR"]).is_dir()
    issue = CommunityIssue(
        26,
        "Olas en la playa",
        "https://upload.wikimedia.org/wikipedia/commons/1/16/"
        "Waves_at_Hikkaduwa_beach%2C_Sri_Lanka.webm",
        "riquelmechile",
        "https://github.com/riquelmechile/clipoteca-comunitaria/issues/26",
        "Olas en la playa de Hikkaduwa, Sri Lanka (Wikimedia Commons). "
        "Prueba E2E de M9.\n\n(reintento tras corrección)",
    )
    candidate = asyncio.run(CommunityContribution(issue).search(issue.title, "es", SearchLimits()))[
        0
    ]
    assert candidate.source_language == "en"
    assert candidate.metadata["description_language"] == "es"
    # An intermittent fail-closed result is acceptable, but repeated corruption
    # must fail this real integration gate rather than pass without translating.
    translated_title = translate_pair(candidate.source_title, "en", "es")
    assert "Hikkaduwa" in translated_title
    assert "Olas" in translated_title

    localized = None
    for _attempt in range(5):
        try:
            localized = localize_candidate(candidate, include_query_tags=False)
        except MetadataTranslationError as exc:
            assert str(exc) in {
                "translation_failed:es->en:disproportionate_translation",
                "translation_failed:es->en:repeated_translation",
            }
        else:
            break
    assert localized is not None, "real Argos never localized candidate after 5 tries"
    assert "Hikkaduwa" in localized.title["es"]
    assert "Waves" in localized.title["en"]
    assert localized.description["es"] != localized.description["en"]
    assert "Hikkaduwa" in localized.description["en"]
    assert localized.tags["es"] and localized.tags["en"]

    translated_query = None
    for _attempt in range(5):
        try:
            translated_query = translate_pair("olas", "es", "en")
        except TranslationFailed as exc:
            assert exc.reason in {"disproportionate_translation", "repeated_translation"}
        else:
            assert translated_query.strip()
            assert translated_query.casefold() != "olas"
            break
    assert translated_query is not None, "real Argos never translated olas after 5 tries"
