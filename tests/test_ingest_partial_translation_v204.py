"""v2.0.4: quality-only expansion failures must not cancel discovery."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipoteca.catalog import Catalog
from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore
from clipoteca.metadata import MetadataTranslationError, localize_candidate
from clipoteca.providers.base import Adapter, Candidate, SearchLimits, SupportStatus
from clipoteca.translate.argos import TranslationFailed, public_translation_error
from fake_secrets import fake_secret


class RecordingAdapter(Adapter):
    name = "recording"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def search(self, term: str, lang: str, limits: SearchLimits):
        self.calls.append((lang, term))
        return []

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


async def run_ingest(tmp_path, monkeypatch, translator):
    monkeypatch.delenv("CLIPOTECA_PERSIST_JOBS", raising=False)
    monkeypatch.setattr(
        "clipoteca.ingest._cached_embedding_encoder",
        lambda _loader: (None, "embeddings_unavailable:m14_quality_fallback"),
    )
    adapter = RecordingAdapter()
    report = await ingest(
        "aurora boreal",
        [adapter],
        repository="test/clipoteca",
        token=fake_secret("partial-translation"),
        writer=SimpleNamespace(read_catalog=lambda: (Catalog(), None)),
        storage=object(),
        job_root=tmp_path / "jobs",
        translator=translator,
    )
    job = JobStore(tmp_path / "jobs").load(report.request_id)
    assert job is not None
    return report, job, adapter.calls


@pytest.mark.asyncio
async def test_disproportionate_zh_only_does_not_abort_ingest(tmp_path, monkeypatch):
    def translator(_text, src, dst):
        return {
            ("es", "en"): "northern lights",
            ("en", "zh"): "untrusted " * 150,
            ("en", "ru"): "северное сияние",
        }[(src, dst)]

    report, job, calls = await run_ingest(tmp_path, monkeypatch, translator)
    assert report.status == "no_results"
    assert report.errors == ()
    assert {lang for lang, _ in calls} == {"es", "en", "ru"}
    assert job["terms"] == {
        "es": "aurora boreal",
        "en": "northern lights",
        "ru": "северное сияние",
    }
    assert job["source_warnings"]["translation:zh"] == (
        "translation_failed:en->zh:disproportionate_translation"
    )
    assert "translation" not in job["source_errors"]


@pytest.mark.asyncio
async def test_all_destination_quality_failures_keep_original_language(tmp_path, monkeypatch):
    report, job, calls = await run_ingest(tmp_path, monkeypatch, lambda *_: "untrusted " * 150)
    assert report.status == "no_results"
    assert calls == [("es", "aurora boreal")]
    assert job["terms"] == {"es": "aurora boreal"}
    assert set(job["source_warnings"]) >= {"translation:en", "translation:zh", "translation:ru"}
    for language in ("en", "zh", "ru"):
        assert job["source_warnings"][f"translation:{language}"] == (
            "translation_failed:es->en:disproportionate_translation"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_translation", "reason"),
    [
        ("", "empty_translation"),
        ("drift" * 5, "repeated_translation"),
        ("aurora boreal", "unchanged_translation"),
    ],
)
async def test_all_quality_reasons_keep_original_language(
    tmp_path, monkeypatch, bad_translation, reason
):
    report, job, calls = await run_ingest(tmp_path, monkeypatch, lambda *_: bad_translation)
    assert report.status == "no_results"
    assert calls == [("es", "aurora boreal")]
    for language in ("en", "zh", "ru"):
        assert job["source_warnings"][f"translation:{language}"] == (
            f"translation_failed:es->en:{reason}"
        )


@pytest.mark.asyncio
async def test_unconfigured_models_still_fail_before_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGOS_MODEL_PINS_JSON", raising=False)
    report, job, calls = await run_ingest(tmp_path, monkeypatch, None)
    assert report.status == "failed"
    assert not calls
    assert report.errors == ("translation_failed:es->en:model_pins_not_configured",)
    assert job["source_errors"]["translation"] == report.errors[0]


@pytest.mark.asyncio
async def test_mixed_quality_and_config_error_still_fails(tmp_path, monkeypatch):
    def mixed(_term, src, dst, *, translator=None):
        if dst == "zh":
            raise TranslationFailed(src, dst, "package_sha256_mismatch")
        if dst == "ru":
            raise TranslationFailed(src, dst, "disproportionate_translation")
        return "northern lights"

    monkeypatch.setattr("clipoteca.translate.argos.translate_pair", mixed)
    report, job, calls = await run_ingest(tmp_path, monkeypatch, lambda *_: "unused")
    assert report.status == "failed"
    assert not calls
    assert "package_sha256_mismatch" in report.errors[0]
    assert job["source_errors"]["translation"] == report.errors[0]


def test_query_terms_are_not_translated_as_tags_and_bilingual_metadata_remains_strict():
    candidate = Candidate(
        "recording",
        "1",
        "aurora boreal",
        "es",
        "https://example.com/clip",
        "Night sky above mountains",
        "en",
    )

    def translator(_term, src, dst):
        if (src, dst) == ("en", "es"):
            return "Cielo nocturno sobre montañas"
        return "untrusted " * 150

    localized = localize_candidate(candidate, translator=translator)
    assert localized.title["en"] == "Night sky above mountains"
    assert localized.title["es"] == "Cielo nocturno sobre montañas"
    assert localized.tags["en"]
    assert "aurora" not in localized.tags["es"]
    assert "boreal" not in localized.tags["es"]

    with pytest.raises(MetadataTranslationError, match="disproportionate_translation"):
        localize_candidate(
            candidate,
            translator=lambda *_: "untrusted " * 150,
        )


def test_public_error_sanitizes_config_reason_with_digits():
    marker = fake_secret("upstream")
    error = "translation_failed:en->zh:package_sha256_mismatch:" + marker
    assert public_translation_error(error) == ("translation_failed:en->zh:package_sha256_mismatch")
