"""Early scene cutoff with simulated providers and a deterministic clock."""

import asyncio

import pytest

from clipoteca.catalog import Catalog
from clipoteca.ingest import IngestReport
from clipoteca.providers.base import Adapter, Candidate, SupportStatus
from clipoteca.studio import scene_search
from clipoteca.translate.expand import QueryExpansion


def job():
    return {
        "len_est": 3,
        "len_fetch": 4,
        "min_short_side": 720,
        "queries": {"en": ["rain movement"], "es": ["lluvia movimiento"]},
        "avoid": [],
    }


def record(title="rain movement", duration=5, width=720):
    return {
        "title": {"en": title},
        "tags": {"en": ["rain"]},
        "media": {"duration_seconds": duration, "width": width, "height": 1280},
    }


def test_budget():
    now = [0]
    records = [record(width=640), record(duration=2), record(title="car")]
    budget = scene_search.SceneBudget(job(), records, 8, 2, 10, clock=lambda: now[0])
    assert budget.valid_count() == 2
    assert budget.reason() is None
    records.extend([record(), record()])
    assert budget.reason() == "enough"
    budget.maximum = 4
    assert budget.reason() == "max_results"
    now[0] = 10
    assert budget.reason() == "timeout"


class Provider(Adapter):
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name, calls, cancelled, slow=False):
        self.name = name
        self.calls = calls
        self.cancelled = cancelled
        self.slow = slow

    async def search(self, term, lang, limits):
        self.calls.append((self.name, lang))
        if self.slow:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.append(self.name)
        return [
            Candidate(
                self.name,
                str(i),
                term,
                lang,
                f"https://example.org/{i}",
                "rain movement",
                lang,
            )
            for i in range(5)
        ]

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


class Writer:
    def __init__(self):
        self.imported = []
        self.catalog = Catalog()

    def read_catalog(self):
        return self.catalog, "local"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("maximum", "enough", "expected"), [(8, 3, "enough"), (2, 3, "max_results")]
)
async def test_cutoff_cancels_inflight_and_avoids_new_batches(monkeypatch, maximum, enough, expected):
    writer = Writer()
    budget = scene_search.SceneBudget(
        job(), writer.imported, maximum, enough, 100, clock=lambda: 0
    )
    calls, cancelled = [], []
    adapters = [
        Provider("slow", calls, cancelled, slow=True),
        Provider("pexels", calls, cancelled),
        Provider("not-started", calls, cancelled),
    ]
    monkeypatch.setattr(
        scene_search,
        "expand_query",
        lambda *args, **kwargs: QueryExpansion(
            "rain", {lang: "rain movement" for lang in ("en", "es", "zh", "ru")}
        ),
    )

    async def fake_ingest(query, adapters, *, discovery, scene_budget, **kwargs):
        ids = []
        for candidate in discovery.candidates:
            if scene_budget.reason():
                break
            writer.imported.append(record())
            scene_budget.downloads_started += 1
            scene_budget.attempted.add(candidate.origin_id)
            ids.append(candidate.origin_id)
        return IngestReport("fixture", "published", tuple(ids), ())

    monkeypatch.setattr(scene_search, "_ingest", fake_ingest)
    result = await scene_search.ingest(
        "rain movement",
        adapters,
        scene_budget=budget,
        writer=writer,
        max_results=maximum,
        horizon="fixture",
        concurrency=2,
    )
    assert result.status == "published"
    assert budget.reason() == expected
    assert len(writer.imported) == min(maximum, enough)
    assert set(calls) == {("pexels", "en"), ("slow", "en")}
    assert cancelled == ["slow"]
    assert budget.searches_cancelled == 1
    assert budget.downloads_started == len(writer.imported)
    assert len(budget.discovered - budget.attempted) == 5 - len(writer.imported)
