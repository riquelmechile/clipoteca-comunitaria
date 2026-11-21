"""Opt-in real, checksum-pinned Argos regression for Issue #26 and ingest query 'olas'."""

import asyncio
import os
from pathlib import Path

import pytest

from clipoteca.contributions import CommunityContribution, CommunityIssue
from clipoteca.metadata import MetadataTranslationError, localize_candidate
from clipoteca.providers.base import SearchLimits
from clipoteca.translate.argos import translate_pair


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
    # The pinned real es->en model is nondeterministic for the longer Issue
    # notes: on x86 it sometimes emits thousands of repeated "mainstre" tokens.
    # A valid translation may pass; a corrupted one MUST fail closed. Neither
    # outcome should make this integration gate flaky.
    translated_title = translate_pair(candidate.source_title, "en", "es")
    assert "Hikkaduwa" in translated_title
    assert "Olas" in translated_title
    try:
        localized = localize_candidate(candidate, include_query_tags=False)
    except MetadataTranslationError as exc:
        assert str(exc) in {
            "translation_failed:es->en:disproportionate_translation",
            "translation_failed:es->en:repeated_translation",
        }
    else:
        assert "Hikkaduwa" in localized.title["es"]
        assert "Waves" in localized.title["en"]
        assert localized.description["es"] != localized.description["en"]
        assert "Hikkaduwa" in localized.description["en"]
        assert localized.tags["es"] and localized.tags["en"]
    # Ordinary ingest 'olas' uses the same real es->en pinned model.
    translated_query = translate_pair("olas", "es", "en")
    assert translated_query.casefold() != "olas"
    assert translated_query.strip()
