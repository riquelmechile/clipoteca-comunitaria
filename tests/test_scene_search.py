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
    budget = scene_search.SceneBudget(
        job(), records, 8, 2, 10, clock=lambda: now[0], visual_match=lambda _: True
    )
    assert budget.valid_count() == 2
    assert budget.reason() is None
    records.extend([record(), record()])
    assert budget.reason() == "enough"
    budget.maximum = 2
    assert budget.reason() == "max_results"
    now[0] = 10
    assert budget.reason() == "timeout"


class Provider(Adapter):
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name, calls, cancelled, slow=False):
        self.name = name
        self.discovery_priority = 100 if name == "pexels" else 0
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
async def test_cutoff_cancels_inflight_and_avoids_new_batches(
    monkeypatch, maximum, enough, expected
):
    writer = Writer()
    budget = scene_search.SceneBudget(
        job(), writer.imported, maximum, enough, 100, clock=lambda: 0, visual_match=lambda _: True
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


def test_relevance_and_digest_dedupe():
    settings = job()
    settings["queries"] = {
        "en": ["rainy night city street neon"],
        "es": ["calle ciudad noche lluvia neon"],
    }
    records = [
        record("Night sky time lapse"),
        record("Street food vendor"),
        record("City traffic aerial"),
    ]
    budget = scene_search.SceneBudget(
        settings, records, 8, 3, 100, clock=lambda: 0, visual_match=lambda _: True
    )
    assert not any(budget.good(item) for item in records)
    settings["queries"] = {"es": ["moais Rapa Nui"], "en": ["Rapa Nui moai"]}
    wrong = record("Our Lady of Nui Cui statue")
    right = record("Moai Rapa Nui")
    assert not budget.good(wrong)
    assert budget.good(right)
    right["media"]["sha256"] = "a" * 64
    records[:] = [right, right.copy()]
    assert budget.valid_count() == 1
    assert budget.reason() is None


@pytest.mark.asyncio
async def test_place_consults_commons_before_relevance_cutoff(monkeypatch):
    writer = Writer()
    settings = job()
    settings["queries"] = {"en": ["Easter Island moai"], "es": ["Rapa Nui moai"]}
    budget = scene_search.SceneBudget(
        settings,
        writer.imported,
        1,
        1,
        100,
        clock=lambda: 0,
        visual_match=lambda row: row["media"].get("sha256") == "good",
    )
    calls, cancelled = [], []
    adapters = [Provider("pexels", calls, cancelled), Provider("wikimedia_commons", calls, cancelled)]
    monkeypatch.setattr(
        scene_search,
        "expand_query",
        lambda *args, **kwargs: QueryExpansion("Easter Island moai", {"en": "Easter Island moai"}),
    )

    async def fake_ingest(query, adapters, *, discovery, scene_budget, **kwargs):
        ids = []
        for candidate in discovery.candidates:
            if scene_budget.reason():
                break
            title = "Easter Island moai"
            row = record(title)
            row["media"]["sha256"] = (
                "good" if candidate.provider == "wikimedia_commons" and ids else candidate.origin_id
            )
            writer.imported.append(row)
            ids.append(candidate.origin_id)
        return IngestReport("fixture", "published", tuple(ids), ())

    monkeypatch.setattr(scene_search, "_ingest", fake_ingest)
    await scene_search.ingest(
        "Easter Island moai",
        adapters,
        scene_budget=budget,
        writer=writer,
        max_results=1,
        horizon="fixture",
        concurrency=2,
    )
    assert calls == [("wikimedia_commons", "en")]
    assert budget.commons_consulted
    assert budget.valid_count() == 2
    assert budget.relevant_count() == 1
    assert budget.reason() == "max_results"


def test_raw_results_do_not_count_and_visual_gate_is_cached():
    records = [record("rain movement") for _ in range(8)]
    checked = []
    budget = scene_search.SceneBudget(
        job(),
        records,
        2,
        2,
        100,
        clock=lambda: 0,
        visual_match=lambda row: checked.append(row) or False,
    )
    assert budget.valid_count() == 8
    assert budget.reason() is None
    assert budget.relevant_count() == 0
    assert len(checked) == 8
