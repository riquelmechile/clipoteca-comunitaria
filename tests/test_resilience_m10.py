"""M10 bounded retries: never retry published, duplicate, or dead-letter jobs."""

import pytest

from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore, MAX_ATTEMPTS
from clipoteca.providers.base import Adapter, Candidate, RateLimited, SupportStatus


class Writer:
    def read_catalog(self):
        from clipoteca.catalog import Catalog

        return Catalog(), "sha"


class Storage:
    def close(self):
        pass


class Flood(Adapter):
    name = "flood"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self):
        self.search_calls = 0
        self.media_calls = 0

    async def search(self, term, lang, limits):
        self.search_calls += 1
        return [
            Candidate(self.name, "id1", term, lang, "https://public.example.org/v",
                      "Lluvia", "es")
        ]

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        self.media_calls += 1
        raise RateLimited("429")


@pytest.mark.asyncio
async def test_three_attempts_then_dead_letter_without_unbounded_calls(tmp_path):
    source = Flood()
    seen = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        report = await ingest(
            "lluvia", [source], repository="example/repo", token="test",
            job_root=tmp_path, writer=Writer(), storage=Storage(),
        )
        seen.append(report.status)
        job = JobStore(tmp_path).load(report.request_id)
        assert job["attempt"] == attempt
    assert seen == ["rate_limited", "rate_limited", "dead_letter"]
    before = source.search_calls
    fourth = await ingest(
        "lluvia", [source], repository="example/repo", token="test",
        job_root=tmp_path, writer=Writer(), storage=Storage(),
    )
    assert fourth.status == "dead_letter"
    assert source.search_calls == before
    record = JobStore(tmp_path).load(fourth.request_id)
    with pytest.raises(ValueError, match="terminal"):
        JobStore(tmp_path).save({**record, "status": "queued", "attempt": 4})


def test_dead_letter_transition_requires_retry_budget(tmp_path):
    from clipoteca.jobs import new_job

    rid = "a" * 64
    store = JobStore(tmp_path)
    active = new_job(rid)
    active["status"] = "rate_limited"
    store.save(active)
    with pytest.raises(ValueError, match="terminal"):
        store.save({**active, "status": "dead_letter"})
    with pytest.raises(ValueError, match="terminal"):
        store.save({**active, "status": "queued", "attempt": 5})
