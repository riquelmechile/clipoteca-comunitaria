"""Offline M8b job outcomes, metrics allowlist, and provider statistics."""

import json

import pytest

from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore
from clipoteca.metrics import write_metrics
from clipoteca.providers.base import Adapter, Candidate, RateLimited, SupportStatus


class CatalogWriter:
    def read_catalog(self):
        from clipoteca.catalog import Catalog

        return Catalog(), "test-sha"


class Storage:
    def close(self):
        pass


class FailingMedia(Adapter):
    name = "failing_media"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    async def search(self, term, lang, limits):
        return [Candidate(self.name, "id1", term, lang, "https://example.com/1", "Lluvia", "es")]

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        raise RateLimited("429")


@pytest.mark.asyncio
async def test_direct_media_429_is_rate_limited_and_not_failed(tmp_path):
    report = await ingest(
        "lluvia",
        [FailingMedia()],
        repository="example/repo",
        token="test",
        job_root=tmp_path,
        writer=CatalogWriter(),
        storage=Storage(),
    )
    assert report.status == "rate_limited"
    job = JobStore(tmp_path).load(report.request_id)
    assert job["status"] == "rate_limited"
    assert job["attempt"] == 1
    metrics = json.loads((tmp_path / f"{report.request_id}.metrics.json").read_text())
    assert metrics["failed_imports"] >= 1
    assert metrics["provider_success_rate"] == 1.0
    assert metrics["provider_timeout_rate"] == 0.0


def test_metric_writer_rejects_sensitive_or_infinite_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    path = tmp_path / "metrics.json"
    for bad in (
        {"token": "secret"},
        {"new_candidates": float("inf")},
        {"provider_search_attempts": {"https://private.example/key": 1}},
    ):
        with pytest.raises(ValueError):
            write_metrics(bad, path)
        assert not path.exists()
    write_metrics({"new_candidates": 1, "failed_imports": 0}, path)
    assert json.loads(path.read_text()) == {"new_candidates": 1, "failed_imports": 0}
