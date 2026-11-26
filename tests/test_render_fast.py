"""Fast import tests: no network, no real media or model downloads."""

import asyncio
import json

import pytest

from clipoteca.ingest import IngestReport
from clipoteca.providers.base import Candidate
from clipoteca.studio import importer, scene_search
from clipoteca.studio.selection import select_scene
from clipoteca.translate.expand import QueryExpansion
from test_import_m28 import FakeAdapter, _catalog, load
from test_scene_search import Provider, Writer, job, record
from test_selection_v3 import row


def candidate(identifier, title, *, language="es", metadata=None):
    return Candidate(
        "fixture",
        identifier,
        "consulta",
        language,
        f"https://media.example/{identifier}",
        title,
        language,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_fast_consumes_ready_provider_before_slow_priority(monkeypatch):
    writer = Writer()
    calls, cancelled = [], []
    budget = scene_search.SceneBudget(
        job(),
        writer.imported,
        8,
        1,
        100,
        clock=lambda: 0,
        visual_match=lambda _: True,
        fast=True,
    )
    adapters = [
        Provider("pexels", calls, cancelled, slow=True),
        Provider("ready", calls, cancelled),
        Provider("unstarted", calls, cancelled),
    ]
    monkeypatch.setattr(
        scene_search,
        "expand_query",
        lambda *args, **kwargs: QueryExpansion(
            "rain", {lang: "rain movement" for lang in ("en", "es", "zh", "ru")}
        ),
    )
    evaluated = []

    async def fake_ingest(query, adapters, *, discovery, scene_budget, **kwargs):
        for candidate in discovery.candidates:
            if scene_budget.reason():
                break
            evaluated.append(candidate.provider)
            writer.imported.append(record())
        return IngestReport("fixture", "published", ("one",), ())

    monkeypatch.setattr(scene_search, "_ingest", fake_ingest)
    result = await asyncio.wait_for(
        scene_search.ingest(
            "rain movement",
            adapters,
            scene_budget=budget,
            writer=writer,
            max_results=8,
            horizon="fixture",
            concurrency=2,
        ),
        timeout=2,
    )
    assert result.status == "published"
    assert evaluated == ["ready"]
    assert budget.reason() == "enough"
    assert cancelled == ["pexels"]
    assert not any(name == "unstarted" for name, _ in calls)


def test_budget_fair_share_and_early_fallback():
    now = [0.0]
    budget = scene_search.SceneBudget(
        job(),
        [],
        8,
        1,
        100,
        clock=lambda: now[0],
        visual_match=lambda _: False,
        fast=True,
        early_deadline=40,
    )
    assert budget.term_seconds(4) == 10
    now[0] = 10
    budget.term_deadline = 10
    assert budget.reason() == "term_timeout"
    budget.term_deadline = None
    assert budget.term_seconds(3) == 10
    now[0] = 40
    assert budget.reason() == "early_fallback"
    assert budget.term_seconds(3) == 0
    # A completed evaluation (even a rejection) restores the remaining full budget.
    budget.visual_cache["checked"] = False
    assert budget.reason() is None
    assert budget.term_seconds(3) == 20
    now[0] = 100
    assert budget.reason() == "timeout"


def test_prefilter_accepts_unaccented_place_without_translation():
    settings = job()
    settings["queries"] = {
        "es": ["Valparaíso puerto cerros"],
        "en": ["Valparaiso Chile colorful hills port"],
    }
    budget = scene_search.SceneBudget(settings, [], 3, 1, 100, clock=lambda: 0)
    item = candidate("valparaiso", "Aerial view of VALPARAISO and its coast")

    assert budget.candidate_prefilter(item) is True
    assert budget.candidates_prefiltered == 1
    assert budget.prefilter_hits == 1
    assert budget.candidates_translated == 0


def test_translation_cache_and_per_term_limit():
    budget = scene_search.SceneBudget(job(), [], 3, 1, 100, clock=lambda: 0)
    budget.translation_limit = 2
    budget.begin_term("rain")
    calls = []

    def translate(text, source, target):
        calls.append((text, source, target))
        return "lluvia"

    cached = budget.cached_translator(translate)
    assert cached("rain", "en", "es") == "lluvia"
    assert cached("rain", "en", "es") == "lluvia"
    assert calls == [("rain", "en", "es")]
    assert budget.translation_cache_hits == 1
    assert budget.reserve_translation(candidate("one", "One"))
    assert budget.reserve_translation(candidate("two", "Two"))
    assert not budget.reserve_translation(candidate("three", "Three"))
    assert budget.candidates_translated == 2
    assert budget.rejections["translation_limit"] == 1


def test_fast_stops_after_first_primary_when_catalog_fallback_exists(tmp_path, monkeypatch):
    settings = load("job.json")
    settings["queries"] = {"en": ["rain", "storm"], "es": []}
    tried = []

    async def no_promising(query, adapters, **kwargs):
        tried.append(query)
        return IngestReport("fixture", "no_results", (), ())

    monkeypatch.setattr(importer, "ingest", no_promising)
    monkeypatch.setattr(importer.library, "candidates", lambda *_: [{"fallback": True}])
    result = importer.import_scene(
        settings,
        tmp_path / "fast",
        catalog_path=_catalog(tmp_path),
        adapters=[FakeAdapter()],
        timeout_s=10,
        fast=True,
    )

    assert tried == ["rain"]
    assert result["termination_reason"] == "no_promising_candidates"


def test_fast_import_moves_past_slow_seed_and_stops_early(tmp_path, monkeypatch):
    settings = load("job.json")
    settings["queries"] = {"en": ["rain movement", "cloud movement"], "es": ["lluvia"]}
    tried = []

    async def slow(query, adapters, **kwargs):
        tried.append(query)
        await asyncio.Event().wait()

    monkeypatch.setattr(importer, "ingest", slow)
    out = tmp_path / "fast"
    result = importer.import_scene(
        settings,
        out,
        catalog_path=_catalog(tmp_path),
        adapters=[FakeAdapter()],
        timeout_s=1.5,
        fast=True,
    )
    assert len(tried) == 3
    assert result["termination_reason"] == "early_fallback"
    assert result["evaluated_count"] == 0
    assert result["rejections"]["term_timeout"] >= 2
    assert result["status"] == "no_results"
    assert result["time_s"] < 1.5
    assert (out / "stage-importer.json").is_file()


@pytest.mark.parametrize("usable", [True, False])
def test_early_fallback_skips_retry_only_with_usable_clip(tmp_path, usable):
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "assignment.json").write_text("{}")

    def runner(job, assignment, out, **kwargs):
        out.mkdir(parents=True, exist_ok=True)
        result = row(0.25, 3) if usable else {"status": "fail"}
        (out / "result.json").write_text(json.dumps(result))
        if usable:
            (out / "clip.mp4").write_bytes(b"catalogue")
        return result

    select_scene(tmp_path, "catalog", runner=runner)
    imp = tmp_path / "import"
    imp.mkdir()
    (imp / "imported.json").write_text(json.dumps({"termination_reason": "early_fallback"}))
    result = select_scene(tmp_path, "finish", runner=runner)
    assert result["needs_retry"] is not usable
    if usable:
        assert result["fallback"] is True
        assert (tmp_path / "clip.mp4").read_bytes() == b"catalogue"


@pytest.mark.asyncio
async def test_fast_bounds_stalled_commons_without_faking_consultation(monkeypatch):
    writer = Writer()
    calls, cancelled = [], []
    settings = job()
    settings["queries"] = {"en": ["Easter Island moai"], "es": ["Rapa Nui moai"]}
    budget = scene_search.SceneBudget(
        settings,
        writer.imported,
        8,
        1,
        10,
        clock=lambda: 0,
        visual_match=lambda _: True,
        fast=True,
        term_deadline=0.1,
    )
    adapters = [
        Provider("pexels", calls, cancelled),
        Provider("wikimedia_commons", calls, cancelled, slow=True),
    ]
    monkeypatch.setattr(
        scene_search,
        "expand_query",
        lambda *args, **kwargs: QueryExpansion(
            "Easter Island moai", {lang: "Easter Island moai" for lang in ("en", "es", "zh", "ru")}
        ),
    )

    async def fake_ingest(query, adapters, *, discovery, scene_budget, **kwargs):
        assert discovery.candidates[0].provider == "pexels"
        writer.imported.append(record("Easter Island moai"))
        return IngestReport("fixture", "published", ("one",), ())

    monkeypatch.setattr(scene_search, "_ingest", fake_ingest)
    result = await asyncio.wait_for(
        scene_search.ingest(
            "Easter Island moai",
            adapters,
            scene_budget=budget,
            writer=writer,
            max_results=8,
            horizon="fixture",
            concurrency=2,
        ),
        timeout=2,
    )
    assert result.status == "published"
    assert calls[:2] == [("wikimedia_commons", "en"), ("pexels", "en")]
    assert cancelled == ["wikimedia_commons"]
    assert not budget.commons_consulted
    assert budget.commons_exhausted
    assert budget.rejections["commons_budget"] == 1
    assert budget.reason() == "enough"
