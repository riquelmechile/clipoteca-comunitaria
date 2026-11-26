"""Pinned real Argos Issue #26: bounded retries and fail-closed invariants.

Deterministic title/description content is asserted only with an injected
translator, not probabilistic offline Argos neural output.
"""

import asyncio
import os
import re
from pathlib import Path

import pytest

from clipoteca.contributions import CommunityContribution, CommunityIssue
from clipoteca.media_ingest import enrich_metadata
from clipoteca.metadata import MetadataTranslationError, localize_candidate
from clipoteca.providers.base import Candidate, SearchLimits
from clipoteca.translate.argos import TranslationFailed, translate_pair

BAD_REASONS = {"disproportionate_translation", "repeated_translation"}


def _synthetic_candidate() -> Candidate:
    return Candidate(
        provider="community",
        provider_id="26",
        term="olas",
        language="es",
        source_url="https://commons.wikimedia.org/wiki/File:Waves_at_Hikkaduwa_beach.webm",
        source_title="Waves at Hikkaduwa beach, Sri Lanka",
        source_language="en",
        metadata={
            "description": "Olas en la playa de Hikkaduwa, Sri Lanka",
            "description_language": "es",
        },
    )


def _fixed_translation(_term: str, src: str, dst: str) -> str:
    if (src, dst) == ("en", "es"):
        return "Olas en la playa de Hikkaduwa, Sri Lanka"
    if (src, dst) == ("es", "en"):
        return "Waves at Hikkaduwa beach, Sri Lanka"
    raise AssertionError(f"unexpected {src}->{dst}")


def test_issue26_exact_content_with_injected_deterministic_translator():
    localized = localize_candidate(
        _synthetic_candidate(), translator=_fixed_translation, include_query_tags=False
    )
    assert "Olas" in localized.title["es"]
    assert "Hikkaduwa" in localized.title["es"]
    assert "Waves" in localized.title["en"]
    assert "Hikkaduwa" in localized.description["en"]
    assert localized.tags["es"] and localized.tags["en"]


def test_ordinary_olas_accepts_translation_and_rejects_corruption():
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


def _nondegenerate(value: str) -> bool:
    return bool(value.strip()) and not re.search(r"(.{3,24}?)\1{3,}", value, flags=re.IGNORECASE)


def _translated_with_retries(text: str, src: str, dst: str) -> str | None:
    for _ in range(5):
        try:
            return translate_pair(text, src, dst)
        except TranslationFailed as exc:
            # Pin errors and unexpected runtime errors are not transient model
            # corruption, and must not be disguised as a successful E2E test.
            if exc.reason not in BAD_REASONS:
                raise
    return None  # repeated model corruption stays fail-closed


@pytest.mark.skipif(
    os.getenv("ARGOS_REAL_INTEGRATION") != "1",
    reason="requires preverified real Argos models and sentence resources",
)
@pytest.mark.parametrize(
    "description",
    [
        "Un perro bulldog franc\u00e9s corriendo en el pasto",
        "Patr\u00f3n de colores de prueba en formato vertical",
    ],
)
def test_real_argos_upload_metadata(description):
    original = {"description": {"es": description}}
    result = enrich_metadata(original)
    assert result["description"]["es"] == description
    english = result["description"].get("en", "")
    assert english.strip() and english.casefold() != description.casefold()
    assert result["tags"]["es"] and result["tags"]["en"]
    assert result["metadata_auto"] == ["description.en", "tags.es", "tags.en"]
    assert original == {"description": {"es": description}}

@pytest.mark.skipif(
    os.getenv("ARGOS_REAL_INTEGRATION") != "1",
    reason="requires preverified real Argos models and sentence resources",
)
def test_real_argos_issue26_and_ordinary_olas(monkeypatch):
    assert Path(os.environ["ARGOS_MODEL_PINS_JSON"]).is_file()
    assert Path(os.environ["ARGOS_MODEL_CACHE_DIR"]).is_dir()
    # These settings are honored by most native CPU threading backends. Argos
    # does not expose a stable public beam-width parameter in this adapter.
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    monkeypatch.setenv("CT2_INTER_THREADS", "1")
    monkeypatch.setenv("CT2_INTRA_THREADS", "1")

    issue = CommunityIssue(
        26,
        "Olas en la playa",
        "https://upload.wikimedia.org/wikipedia/commons/1/16/"
        "Waves_at_Hikkaduwa_beach%2C_Sri_Lanka.webm",
        "riquelmechile",
        "https://github.com/riquelmechile/clipoteca-comunitaria/issues/26",
        "Olas en la playa de Hikkaduwa, Sri Lanka (Wikimedia Commons). "
        "Prueba E2E de M9.\n\n(reintento tras correcci\u00f3n)",
    )
    candidate = asyncio.run(CommunityContribution(issue).search(issue.title, "es", SearchLimits()))[
        0
    ]
    assert candidate.source_language == "en"
    assert candidate.metadata["description_language"] == "es"

    translated_title = _translated_with_retries(candidate.source_title, "en", "es")
    if translated_title is not None:
        assert _nondegenerate(translated_title)

    localized = None
    transient_errors: list[str] = []
    for _attempt in range(5):
        try:
            localized = localize_candidate(candidate, include_query_tags=False)
        except MetadataTranslationError as exc:
            code = str(exc)
            assert any(code.endswith(":" + reason) for reason in BAD_REASONS), code
            transient_errors.append(code)
        else:
            break
    if localized is not None:
        for language in ("es", "en"):
            assert localized.title[language].strip()
            assert localized.description[language].strip()
            assert localized.tags[language]
            assert _nondegenerate(localized.title[language])
            assert _nondegenerate(localized.description[language])
    else:
        assert len(transient_errors) == 5  # no corrupt metadata was returned

    translated_query = _translated_with_retries("olas", "es", "en")
    if translated_query is not None:
        assert _nondegenerate(translated_query)
        assert translated_query.casefold() != "olas"
    # A fully empty run does not prove the real Argos integration works.
    # Bounded failures remain fail-closed, but at least one real translation
    # must succeed to satisfy this smoke test.
    assert translated_title is not None or localized is not None or translated_query is not None, (
        "all real Argos translations failed after bounded retries"
    )
