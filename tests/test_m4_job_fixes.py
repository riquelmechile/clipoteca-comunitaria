"""Request canonicalization and negative ingestion outcomes."""

import pytest

from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore, request_id
from clipoteca.providers.base import (
    Adapter,
    AuthRequired,
    RateLimited,
    SupportStatus,
)


class FakeWriter:
    def read_catalog(self):
        from clipoteca.catalog import Catalog

        return Catalog(), "test-sha"


class Broken(Adapter):
    name = "broken"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def search(self, term, lang, limits):
        self.calls += 1
        if self.error == "429":
            raise RateLimited()
        if self.error == "auth":
            raise AuthRequired()
        return []

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


class FakeStorage:
    def close(self):
        pass


def test_request_id_canonical_normalization():
    a = request_id(" LLÚVIA ", {"max_duration": "20", "aspect_ratio": ""}, "v1")
    b = request_id("lluvia", {"aspect_ratio": None, "max_duration": 20.0}, "v1")
    c = request_id("ＬＬＵＶＩＡ", {"max_duration": 20}, "v1")
    assert a == b
    assert c != a
    assert request_id("LLÚVIA", {}, "v1") == request_id("lluvia", {}, "v1")


@pytest.mark.asyncio
async def test_ingest_translation_failure_is_terminal_and_skips_providers(tmp_path):
    adapter = Broken("429")

    def fail(_text, _source, _target):
        raise RuntimeError("missing local model")

    report = await ingest(
        "a novel unknown phrase",
        [adapter],
        repository="test/repo",
        token="fake",
        job_root=tmp_path,
        writer=FakeWriter(),
        storage=FakeStorage(),
        translator=fail,
    )
    assert report.status == "failed"
    assert adapter.calls == 0
    job = JobStore(tmp_path).load(report.request_id)
    assert job["source_errors"]["translation"].startswith("translation_failed:")
    assert job["terms"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,expected", [("429", "rate_limited"), ("auth", "unavailable"), ("none", "no_results")]
)
async def test_ingest_no_candidates_have_truthful_terminal_state(tmp_path, error, expected):
    report = await ingest(
        "lluvia",
        [Broken(error)],
        repository="test/repo",
        token="fake",
        job_root=tmp_path,
        writer=FakeWriter(),
        storage=FakeStorage(),
    )
    assert report.status == expected


@pytest.mark.asyncio
async def test_ingest_rejects_duration_above_limit(tmp_path):
    with pytest.raises(ValueError, match="max_duration"):
        await ingest(
            "lluvia",
            [],
            repository="test/repo",
            token="fake",
            job_root=tmp_path,
            filters={"max_duration": 21},
        )
