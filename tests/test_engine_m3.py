from __future__ import annotations

import asyncio
import json
import time
from collections import Counter

import pytest
from clipoteca.catalog import Catalog
from clipoteca.engine import SearchEngine
from clipoteca.jobs import JobStore, new_job, request_id
from clipoteca.metrics import discovery_metrics, write_metrics
from clipoteca.providers.base import (
    Adapter,
    Candidate,
    MediaRef,
    RateLimited,
    SearchLimits,
    SupportStatus,
)


class FakeAdapter(Adapter):
    name = "fake"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, *, delay: float = 0, error: str = "", counter=None):
        self.delay = delay
        self.error = error
        self.received = []
        self.counter = counter

    async def search(self, term: str, lang: str, limits: SearchLimits):
        self.received.append((term, lang))
        if self.counter:
            self.counter["active"] += 1
            self.counter["max"] = max(self.counter["max"], self.counter["active"])
        try:
            await asyncio.sleep(self.delay)
            if self.error == "429":
                raise RateLimited("rate_limited")
            if self.error == "500":
                raise RuntimeError("500")
            return [Candidate(self.name, term, term, lang, "https://example.com/video", term, lang)]
        finally:
            if self.counter:
                self.counter["active"] -= 1

    async def metadata(self, candidate):
        return {"id": candidate.provider_id}

    async def direct_media(self, candidate):
        return MediaRef("https://cdn.example.com/video.mp4", "video/mp4")


@pytest.mark.asyncio
async def test_four_languages_exact_for_rain():
    adapter = FakeAdapter()
    result = await SearchEngine([adapter]).search("lluvia")
    assert set(adapter.received) == {
        ("lluvia", "es"),
        ("rain", "en"),
        ("下雨", "zh"),
        ("дождь", "ru"),
    }
    assert len(result.candidates) == 4
    assert not result.expansion.errors


@pytest.mark.asyncio
async def test_concurrent_limit_and_hung_adapter_isolated():
    counter = {"active": 0, "max": 0}
    normal = FakeAdapter(delay=0.01, counter=counter)
    normal.name = "normal"
    hung = FakeAdapter(delay=20, counter=counter)
    hung.name = "hung"
    started = time.monotonic()
    result = await SearchEngine(
        [normal, hung],
        concurrency=4,
        per_source_seconds=0.035,
        discovery_seconds=0.15,
    ).search("lluvia")
    assert time.monotonic() - started < 1
    assert counter["max"] <= 4
    assert any(entry.status == SupportStatus.TIMEOUT for entry in result.executions)
    assert any(entry.provider == "normal" for entry in result.executions)
    assert len(result.candidates) == 4


@pytest.mark.asyncio
async def test_provider_own_timeouts_replace_old_fixed_global_deadline():
    stuck = FakeAdapter(delay=10)
    started = time.monotonic()
    result = await SearchEngine([stuck], discovery_seconds=0.03, per_source_seconds=0.025).search(
        "lluvia"
    )
    assert time.monotonic() - started < 0.5
    assert {entry.status for entry in result.executions} == {SupportStatus.TIMEOUT}
    assert {entry.error for entry in result.executions} == {"provider_timeout"}


@pytest.mark.asyncio
async def test_429_and_500_are_isolated_statuses():
    limited = FakeAdapter(error="429")
    limited.name = "limited"
    failed = FakeAdapter(error="500")
    failed.name = "failed"
    okay = FakeAdapter()
    result = await SearchEngine([limited, failed, okay], concurrency=4).search("lluvia")
    grouped = {}
    for execution in result.executions:
        grouped.setdefault(execution.provider, set()).add(execution.status)
    assert SupportStatus.RATE_LIMITED in grouped["limited"]
    assert SupportStatus.UNAVAILABLE in grouped["failed"]
    assert SupportStatus.SUPPORTED_SEARCH_DOWNLOAD in grouped["fake"]
    assert result.candidates


@pytest.mark.asyncio
async def test_catalogued_candidate_never_resolved_or_returned():
    adapter = FakeAdapter()
    catalog = Catalog(
        [
            {
                "id": "clip-000001",
                "status": "published",
                "source": {"provider": "fake", "provider_id": "rain"},
                "title": {"es": "Lluvia", "en": "Rain"},
                "description": {"es": "Calle", "en": "Street"},
                "tags": {"es": [], "en": []},
                "media": {"sha256": "a" * 64},
            }
        ]
    )
    result = await SearchEngine([adapter]).search("rain", catalog=catalog)
    assert len(result.local) == 1
    assert len(result.candidates) == 3
    assert result.duplicates == 1


@pytest.mark.asyncio
async def test_metrics_and_real_job_schema(tmp_path, monkeypatch):
    result = await SearchEngine([FakeAdapter()]).search("lluvia")
    payload = discovery_metrics(result)
    assert payload["search_requests_by_language"] == {"es": 1, "en": 1, "zh": 1, "ru": 1}
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    write_metrics(payload, tmp_path / "metrics.json")
    assert "search_requests_total" in summary.read_text()
    rid = request_id(" lluvia ", {"max_duration": 20}, "2026-11-20")
    assert rid == request_id("LLUVIA", {"max_duration": 20}, "2026-11-20")
    store = JobStore(tmp_path / "jobs")
    job = new_job(rid)
    store.save(job)
    assert store.load(rid) == job
    job["status"] = "searching"
    job["language_counts"] = Counter(entry.language for entry in result.executions)
    store.save(job)
    job["status"] = "published"
    store.save(job)
    with pytest.raises(ValueError, match="terminal"):
        store.save({**job, "status": "failed"})


def test_cli_dry_run_real(capsys):
    from clipoteca.cli import main

    assert main(["search", "lluvia", "--dry-run", "--aspect-ratio", "9:16"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["terms"] == {
        "es": "lluvia",
        "en": "rain",
        "zh": "下雨",
        "ru": "дождь",
    }
    assert result["filters"]["aspect_ratio"] == "9:16"
