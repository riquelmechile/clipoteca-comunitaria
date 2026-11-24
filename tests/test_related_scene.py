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
