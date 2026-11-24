"""Offline Wikidata/Commons related discovery and explicit term precedence."""

import asyncio

import httpx

from clipoteca.studio.related import MAX_RELATED, related_terms, resolve_jobs


def test_related_wikidata_fixtures_and_render_cache():
    calls = []

    def response(request):
        params = request.url.params
        calls.append((request.url.host, params.get("search")))
        if request.url.host == "www.wikidata.org":
            search = params["search"]
            qid = "Q1" if search == "chiloe" else "Q2"
            return httpx.Response(200, json={"search": [{"id": qid, "label": search}]})
        if request.url.host == "query.wikidata.org":
            terms = (
                ["Castro", "Ancud", "Chonchi", "Dalcahue"]
                if "wd:Q1" in params["query"]
                else ["Hanga Roa", "Ahu Tongariki", "Rano Raraku", "Anakena"]
            )
            return httpx.Response(
                200,
                json={"results": {"bindings": [{"itemLabel": {"value": t}} for t in terms]}},
            )
        return httpx.Response(503)

    async def run():
        jobs = [
            {"queries": {"es": ["Chilo\u00e9"], "en": []}},
            {"queries": {"es": [], "en": ["Easter Island"]}},
            {"queries": {"es": ["Chilo\u00e9"], "en": [], "related": {"es": ["Palafitos"]}}},
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await resolve_jobs(jobs, client=client)
        assert [row["term"] for row in related_terms(jobs[0])] == [
            "Castro",
            "Ancud",
            "Chonchi",
            "Dalcahue",
        ]
        assert [row["term"] for row in related_terms(jobs[1])] == [
            "Hanga Roa",
            "Ahu Tongariki",
            "Rano Raraku",
            "Anakena",
        ]
        assert related_terms(jobs[2])[0] == {"term": "Palafitos", "origin": "script"}
        assert calls.count(("www.wikidata.org", "chiloe")) == 2

    asyncio.run(run())


def test_lookup_failure_preserves_explicit_and_bounds_terms():
    def response(_request):
        return httpx.Response(503)

    async def run():
        job = {"queries": {"en": ["river"], "es": [], "related": {"en": ["waterfall"]}}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await resolve_jobs([job], client=client)
        assert related_terms(job) == [{"term": "waterfall", "origin": "script"}]

    asyncio.run(run())
    job = {"queries": {"en": ["river"], "related": {"es": [str(i) for i in range(20)]}}}
    assert len(related_terms(job)) == MAX_RELATED


def test_related_gate_matches_real_metadata_not_injected_tags():
    from clipoteca.studio.library import sufficient_query_match

    terms = [{"term": "Castro", "origin": "wikidata"}]
    clip = {"title": {"es": "Palafitos de Castro"}}
    assert sufficient_query_match(clip, ["Chilo\u00e9"], terms)
    assert not sufficient_query_match(
        {"title": {"es": "Costa"}, "tags": {"es": ["Castro"]}}, ["Chilo\u00e9"], terms
    )



def test_catalog_related_term_and_primary_tie():
    from clipoteca.studio.library import candidates

    def clip(identifier, title):
        return {
            "id": identifier,
            "status": "published",
            "title": {"es": title},
            "source": {"source_url": "https://example.invalid/source", "source_title": title},
            "media": {
                "duration_seconds": 10,
                "width": 1920,
                "height": 1080,
                "sha256": "a" * 64,
                "size_bytes": 1000,
            },
            "storage": {"asset_url": "https://example.invalid/clip.mp4"},
        }

    job = {
        "len_est": 5,
        "queries": {"es": ["Chilo\u00e9"], "en": [], "related": {"es": ["Castro"]}},
    }
    related = clip("clip-000001", "Iglesia de Castro")
    primary = clip("clip-000002", "Chilo\u00e9")
    fallback = clip("clip-000003", "Costa")
    ranked = candidates(job, [related, fallback])
    assert [row["clip_id"] for row in ranked] == ["clip-000001"]
    assert ranked[0]["matched_query"] == "Castro"
    ranked = candidates(job, [related, fallback, primary])
    assert [row["clip_id"] for row in ranked] == ["clip-000002", "clip-000001"]


def test_related_discovery_parallel_but_ingest_serial(monkeypatch):
    from types import SimpleNamespace

    from clipoteca.engine import SearchResult
    from clipoteca.ingest import IngestReport
    from clipoteca.providers.base import SupportStatus
    from clipoteca.studio import scene_search
    from clipoteca.translate.expand import QueryExpansion

    active = 0
    peak = 0
    writes = 0
    tried = []

    async def run():
        both = asyncio.Event()

        class Engine:
            def __init__(self, *args, **kwargs):
                pass

            async def search(self, query, *, expansion, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), timeout=1)
                active -= 1
                return SearchResult(expansion, [], [], [], 0)

        async def ingest(query, adapters, **kwargs):
            nonlocal writes
            assert writes == 0
            writes += 1
            await asyncio.sleep(0)
            writes -= 1
            return IngestReport(query, "no_results", (), ())

        def expand(query, **kwargs):
            return QueryExpansion(query, {lang: query for lang in ("en", "es", "zh", "ru")})

        monkeypatch.setattr(scene_search, "SearchEngine", Engine)
        monkeypatch.setattr(scene_search, "_ingest", ingest)
        monkeypatch.setattr(scene_search, "expand_query", expand)
        job = {"queries": {"en": ["water"], "es": []}}
        budget = scene_search.SceneBudget(job, [], 8, 3, 100, clock=lambda: 0)
        adapter = SimpleNamespace(
            name="pexels",
            support_status=SupportStatus.SUPPORTED_SEARCH_DOWNLOAD,
            accepts_text_languages={"en"},
        )
        await scene_search.ingest_related(
            ["river", "waterfall"],
            [adapter],
            scene_budget=budget,
            on_query=tried.append,
            writer=SimpleNamespace(read_catalog=lambda: (None, "")),
            horizon="fixture",
            max_results=8,
            concurrency=2,
        )
        assert tried == ["river", "waterfall"]
        assert peak == 2

    asyncio.run(run())
