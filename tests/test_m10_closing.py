"""M10 regression tests for durable terminal statuses and title metadata."""

from datetime import UTC, datetime, timedelta

import pytest

from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore, expired_dead_letter, new_job, request_id
from clipoteca.metadata import _title_tags, humanize_source_title
from clipoteca.providers.base import Adapter, SearchLimits, SupportStatus


class Empty(Adapter):
    name = "empty"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self):
        self.called = 0

    async def search(self, term, lang, limits: SearchLimits):
        self.called += 1
        return []

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


class ExplodingWriter:
    def read_catalog(self):
        raise ConnectionError("Upstream URL and secret must not reach job record")


class Storage:
    def close(self):
        pass


def test_no_results_does_not_spend_retry_budget(tmp_path):
    store = JobStore(tmp_path)
    rid = request_id("empty", {}, "v1")
    record = new_job(rid)
    record["status"] = "no_results"
    store.save(record)
    assert store.load(rid)["attempt"] == 1


@pytest.mark.asyncio
async def test_no_results_returned_without_new_attempt(tmp_path):
    source = Empty()
    rid = request_id("lluvia", {}, "v1")
    record = new_job(rid)
    record["status"] = "no_results"
    JobStore(tmp_path).save(record)
    report = await ingest(
        "lluvia",
        [source],
        repository="example/repo",
        token="test",
        job_root=tmp_path,
        storage=Storage(),
    )
    assert report.status == "no_results"
    assert source.called == 0
    assert JobStore(tmp_path).load(rid)["attempt"] == 1


@pytest.mark.asyncio
async def test_unexpected_read_failure_is_durable_failed_with_safe_reason(tmp_path):
    source = Empty()
    report = await ingest(
        "lluvia",
        [source],
        repository="example/repo",
        token="test",
        job_root=tmp_path,
        writer=ExplodingWriter(),
        storage=Storage(),
        translator=lambda text, _src, _dst: text,
    )
    record = JobStore(tmp_path).load(report.request_id)
    assert report.status == "failed"
    assert record["status"] == "failed"
    assert record["source_errors"]["pipeline"] == "ConnectionError"
    assert "secret" not in str(record)
    assert record["updated_at"].endswith("Z")


def test_dead_letter_expires_after_24_hours():
    row = new_job("a" * 64)
    row["status"] = "dead_letter"
    row["updated_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    assert expired_dead_letter(row)
    row["updated_at"] = datetime.now(UTC).isoformat()
    assert not expired_dead_letter(row)


def test_title_slashes_and_stopwords():
    assert humanize_source_title("Day/Night Scene") == "Day/Night Scene"
    assert humanize_source_title("https://cdn.example.org/video/cold-rain.mp4") == "Cold rain.mp4"
    assert "the" not in [word.casefold() for word in _title_tags("The rain over the street")]
