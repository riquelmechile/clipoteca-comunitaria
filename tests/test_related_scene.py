"""Offline tests with realistic Wikimedia response shapes and synthetic child results."""

import asyncio

import httpx

from clipoteca.studio.related import (
    MAX_RELATED,
    related_search_query,
    related_terms,
    resolve_jobs,
    seeds,
)


def _claim(qid):
    return {
        "mainsnak": {
            "snaktype": "value",
            "datavalue": {"type": "wikibase-entityid", "value": {"id": qid}},
        }
    }


def _entity(label, kind, *, links=20, country=None, coordinates=True):
    claims = {"P31": [_claim(kind)]}
    if country:
        claims["P17"] = [_claim(country)]
    if coordinates:
        claims["P625"] = [
            {
                "mainsnak": {
                    "snaktype": "value",
                    "datavalue": {
                        "type": "globecoordinate",
                        "value": {"latitude": 1, "longitude": 1},
                    },
                }
            }
        ]
    return {
        "labels": {"en": {"language": "en", "value": label}},
        "claims": claims,
        "sitelinks": {f"wiki{i}": {"title": label} for i in range(links)},
    }


def _hit(qid, label, match, description=""):
    return {
        "id": qid,
        "label": label,
        "description": description,
        "match": {"type": "alias", "language": "en", "text": match},
    }


def test_related_wikidata_fixtures_and_render_cache():
    # Chiloé and Kyoto homonyms are based on the reported live wbsearchentities
    # results. Child bindings and claim details are synthetic, not a live capture.
    calls = []
    entities = {
        "Q504449": _entity("Chiloe Island", "Q23442", links=60, country="Q298"),
        "Q201267": _entity("Chiloe Province", "Q56061", links=30, country="Q298"),
        "Q177608": _entity("Chiloe Archipelago", "Q33837", links=20, country="Q298"),
        "Q54832215": _entity("Chiloe constituency", "Q56061", links=100),
        "Q14625303": _entity("Chiloe", "Q16521", links=100, coordinates=False),
        "Q900001": _entity("Easter Island", "Q23442", links=100, country="Q298"),
        "Q900002": _entity("Rapa Nui", "Q7366", links=120, coordinates=False),
        "Q34600": _entity("Kyoto", "Q515", links=100),
        "Q120730": _entity("Kyoto Prefecture", "Q56061", links=70),
        "Q900003": _entity("Kyoto", "Q482994", links=200, coordinates=False),
        "Q2887": _entity("Santiago", "Q515", links=100, country="Q298"),
        "Q900004": _entity("Santiago", "Q515", links=150, country="Q30"),
        "Q298": _entity("Chile", "Q6256", links=200),
    }
    search = {
        "chiloe": [
            _hit("Q504449", "Chiloe Island", "Chiloe"),
            _hit("Q201267", "Chiloe Province", "Chiloe"),
            _hit("Q177608", "Chiloe Archipelago", "Chiloe"),
            _hit("Q54832215", "Chiloe constituency", "Chiloe", "electoral constituency"),
            _hit("Q14625303", "Chiloe", "Chiloe", "genus of insects"),
        ],
        "rapa nui": [
            _hit("Q900001", "Easter Island", "Rapa Nui"),
            _hit("Q900002", "Rapa Nui", "Rapa Nui", "song"),
        ],
        "kyoto": [
            _hit("Q34600", "Kyoto", "Kyoto"),
            _hit("Q120730", "Kyoto Prefecture", "Kyoto"),
            _hit("Q900003", "Kyoto", "Kyoto", "album"),
        ],
        "santiago": [
            _hit("Q2887", "Santiago", "Santiago"),
            _hit("Q900004", "Santiago", "Santiago"),
        ],
        "chile": [_hit("Q298", "Chile", "Chile")],
    }

    # CirrusSearch returns ranked item IDs; wbgetentities resolves their labels.
    child_sets = {
        "Q504449": ["Castro", "Ancud", "Chonchi", "Dalcahue"],
        "Q900001": ["Hanga Roa", "Ahu Tongariki", "Rano Raraku", "Anakena"],
        "Q34600": ["Fushimi Inari", "Kinkaku-ji", "Kiyomizu-dera"],
        "Q2887": ["Providencia", "Cerro San Cristobal", "Plaza de Armas"],
    }
    child_ids = {}
    for root, terms in child_sets.items():
        child_ids[root] = []
        for term in terms:
            qid = f"Q{910000 + sum(len(ids) for ids in child_ids.values())}"
            child_ids[root].append(qid)
            entities[qid] = _entity(term, "Q839954")
            if root == "Q34600":
                entities[qid]["descriptions"] = {"en": {"value": "Buddhist temple"}}

    def response(request):
        params = request.url.params
        host = request.url.host
        calls.append((host, params.get("action"), params.get("search")))
        if host == "www.wikidata.org":
            if params.get("action") == "wbsearchentities":
                return httpx.Response(200, json={"search": search.get(params["search"], [])})
            if params.get("action") == "query":
                statement = params["srsearch"].split()[0].lstrip("(")
                prop, root = statement.removeprefix("haswbstatement:").split("=")
                assert "haswbstatement:P276=" in params["srsearch"]
                assert root not in {"Q14625303", "Q54832215", "Q900002", "Q900003", "Q900004"}
                assert params["srsort"] == "incoming_links_desc"
                if root in {"Q34600", "Q120730"}:
                    assert params["srsearch"].endswith(" (temple OR shrine)")
                ids = child_ids.get(root, []) if prop == "P131" else []
                return httpx.Response(
                    200, json={"query": {"search": [{"title": qid} for qid in ids]}}
                )
            ids = params["ids"].split("|")
            return httpx.Response(200, json={"entities": {qid: entities[qid] for qid in ids}})
        return httpx.Response(503)

    async def run():
        jobs = [
            {"queries": {"es": ["Chilo\u00e9"], "en": []}},
            {"queries": {"es": [], "en": ["Easter Island"]}},
            {"queries": {"es": ["Chilo\u00e9"], "en": [], "related": {"es": ["Palafitos"]}}},
            {"queries": {"es": [], "en": ["Kyoto temples"]}},
            {"queries": {"es": [], "en": ["Santiago Chile city"]}},
            {"queries": {"es": [], "en": ["specialty coffee barista"]}},
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            diagnostics = await resolve_jobs(jobs, client=client)
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
        assert [row["term"] for row in related_terms(jobs[3])] == [
            "Fushimi Inari",
            "Kinkaku-ji",
            "Kiyomizu-dera",
        ]
        assert related_terms(jobs[4])[0]["term"] == "Providencia"
        assert related_terms(jobs[5]) == []
        assert diagnostics[4]["roots"] == [{"qid": "Q2887", "label": "Santiago"}]
        assert not any(call[0] == "query.wikidata.org" for call in calls)
        assert calls.count(("www.wikidata.org", "wbsearchentities", "chiloe")) == 1
        assert related_search_query(jobs[0], "Castro") == "Castro Chilo\u00e9"
        assert related_search_query(jobs[1], "Hanga Roa") == "Hanga Roa Easter Island"
        assert related_search_query(jobs[4], "Providencia") == "Providencia santiago chile"
        assert seeds({"en": ["Santiago Chile city"], "es": ["Chile"]}) == ("santiago",)

    asyncio.run(run())


def test_commons_filters_meta_and_descends_into_landmark_categories():
    # Meta titles are from the reported live run; children model categorymembers.
    direct = [
        "Aerial photographs of Easter Island",
        "Ahu in Easter Island",
        "Books about Easter Island",
        "Culture of Easter Island",
        "Earthquakes in Easter Island",
        "Geography of Easter Island",
        "Geology of Easter Island",
        "2016 Easter Island earthquake",
        "Easter Island in art",
        "Easter Island by media type",
        "Buildings in Santiago de Chile by commune",
        "Things named after Easter Island",
        "Ships named after Easter Island",
        "Islets adjacent to Easter Island",
        "Cultural Properties designated by government",
        "Churches in Ancud",
        "Hare Moas",
        "Hanga Roa",
    ]
    members = {
        "Category:Easter Island": direct,
        "Category:Ahu in Easter Island": ["Ahu Tongariki", "Ahu Akivi", "Books about Ahu"],
        "Category:Geography of Easter Island": ["Rano Raraku", "Anakena", "Rano Kau"],
    }

    def response(request):
        if request.url.host != "commons.wikimedia.org":
            return httpx.Response(503)
        params = request.url.params
        if params.get("list") == "search":
            return httpx.Response(
                200,
                json={"query": {"search": [{"ns": 14, "title": "Category:Easter Island"}]}},
            )
        children = [
            {"ns": 14, "title": f"Category:{title}"} for title in members.get(params["cmtitle"], [])
        ]
        return httpx.Response(200, json={"query": {"categorymembers": children}})

    async def run():
        job = {"queries": {"en": ["Easter Island"], "es": []}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await resolve_jobs([job], client=client)
        terms = {row["term"] for row in related_terms(job)}
        assert {"Ahu Tongariki", "Ahu Akivi", "Rano Raraku", "Anakena", "Hanga Roa"} <= terms
        assert terms.isdisjoint(set(direct) - {"Hare Moas", "Hanga Roa"})
        assert all(row["origin"] == "commons" for row in related_terms(job))

    asyncio.run(run())


def test_lookup_deadline_keeps_explicit_terms():
    async def response(_request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    async def run():
        job = {"queries": {"en": ["Chiloe"], "es": [], "related": {"es": ["Castro"]}}}
        loop = asyncio.get_running_loop()
        start = loop.time()
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            await resolve_jobs([job], client=client, timeout=0.02)
        assert loop.time() - start < 1
        assert related_terms(job) == [{"term": "Castro", "origin": "script"}]

    asyncio.run(run())


def test_each_seed_receives_its_own_budget(monkeypatch):
    from clipoteca.studio import related

    async def wiki(_client, seed, _context, state, **kwargs):
        await asyncio.sleep(0.03)
        state["roots"] = [{"qid": "Q1", "label": seed}]
        return [{"term": f"{seed} landmark", "origin": "wikidata"}]

    async def commons(*args, **kwargs):
        return []

    async def run():
        monkeypatch.setattr(related, "_wikidata", wiki)
        monkeypatch.setattr(related, "_commons", commons)
        monkeypatch.setattr(related, "SEED_CONCURRENCY", 1)
        jobs = [{"queries": {"en": [f"Place {i}"], "es": []}} for i in range(4)]
        async with httpx.AsyncClient() as client:
            diagnostics = await resolve_jobs(jobs, client=client, timeout=0.08)
        assert all(job["related_terms"] for job in jobs)
        assert all(not entry["errors"] for entry in diagnostics)

    asyncio.run(run())


def test_desert_query_does_not_accept_region_homonym():
    roots = {
        "Q2120": _entity("Atacama Region", "Q56061", country="Q298"),
        "Q298": _entity("Chile", "Q6256"),
    }

    def response(request):
        if request.url.host != "www.wikidata.org":
            return httpx.Response(503)
        params = request.url.params
        if params["action"] == "wbsearchentities":
            hits = (
                [_hit("Q298", "Chile", "Chile")]
                if params["search"] == "chile"
                else [_hit("Q2120", "Atacama Region", "Atacama", "region of Chile")]
            )
            return httpx.Response(200, json={"search": hits})
        assert params["action"] == "wbgetentities"
        return httpx.Response(
            200, json={"entities": {qid: roots[qid] for qid in params["ids"].split("|")}}
        )

    async def run():
        job = {"queries": {"en": ["Atacama desert Chile"], "es": []}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            diagnostics = await resolve_jobs([job], client=client)
        assert diagnostics[0]["roots"] == []
        assert job["related_terms"] == []

    asyncio.run(run())


def test_wikimedia_retry_after_and_recovery():
    import time

    from clipoteca.studio import related

    calls = []

    def response(request):
        calls.append(time.monotonic())
        assert request.url.params["maxlag"] == "5"
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.02"})
        return httpx.Response(200, json={"search": [{"id": "Q1"}]})

    async def run():
        errors = []
        token = related._ERRORS.set(errors)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                result = await related._optional_json(
                    client, related.WIKIDATA, action="wbsearchentities"
                )
            assert result["search"] == [{"id": "Q1"}]
            assert len(calls) == 2
            assert calls[1] - calls[0] >= 0.018
            assert errors[0]["http_status"] == 429
            assert errors[0]["retry"] is True
        finally:
            related._ERRORS.reset(token)

    asyncio.run(run())


def test_persistent_error_and_second_opportunity():
    from clipoteca.studio import related

    attempts = []

    def response(request):
        attempts.append(request.url.host)
        return httpx.Response(503, headers={"Retry-After": "0"})

    async def run():
        job = {"queries": {"en": ["Chilo\u00e9"], "es": []}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            diagnostics = await resolve_jobs([job], client=client, timeout=0.3, batch_timeout=2)
        row = diagnostics[0]
        assert job["related_terms"] == []
        assert row == job["related_diagnostic"]
        assert row["status"] == "error"
        assert row["errors"]
        assert any(error["http_status"] == 503 for error in row["errors"])
        assert all(item["attempts"] == 2 for item in row["providers"])
        assert row["batch_elapsed_s"] <= 2.1
        assert len(attempts) >= 4
        assert related._retry_delay("0") == 0

    asyncio.run(run())


def test_ten_slow_scenes_keep_independent_budgets_and_cache(monkeypatch):
    from clipoteca.studio import related

    calls = []
    active = 0
    peak = 0

    async def wiki(_client, seed, _context, _state, **kwargs):
        nonlocal active, peak
        calls.append(seed)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.025)
        active -= 1
        return [{"term": f"{seed} landmark", "origin": "wikidata"}]

    async def commons(*args, **kwargs):
        return []

    async def run():
        monkeypatch.setattr(related, "_wikidata", wiki)
        monkeypatch.setattr(related, "_commons", commons)
        monkeypatch.setattr(related, "SEED_CONCURRENCY", 2)
        jobs = [{"queries": {"en": [f"Place {i}"], "es": []}} for i in range(10)]
        jobs.append({"queries": {"en": ["Place 0"], "es": []}})
        async with httpx.AsyncClient() as client:
            diagnostics = await resolve_jobs(jobs, client=client, timeout=0.08, batch_timeout=0.8)
        assert len(calls) == 10
        assert peak == 2
        assert all(job["related_terms"] for job in jobs)
        assert jobs[0]["related_terms"] == jobs[-1]["related_terms"]
        assert all(row["status"] == "ok" for row in diagnostics)
        assert all(row["batch_elapsed_s"] < 0.8 for row in diagnostics)

    asyncio.run(run())


def test_nonfilmable_terms_filtered():
    from clipoteca.studio.related import _child_term

    for label in (
        "POLARBEAR",
        "Simons Array",
        "Derrumbe de la mina San Francisco",
        "Programa Revivamos las Torres",
        "Hike to Grey Glacier",
        "Animita de Daniel Zamudio",
        "Archivo Nacional",
    ):
        assert _child_term(label, {"chiloe"}) is None
    for label in ("Castro", "Ancud", "Iglesia de Castro", "Cuernos del Paine"):
        assert _child_term(label, {"chiloe"}) == label


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
