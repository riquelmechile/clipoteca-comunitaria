"""Offline persistent cache and indexing contract."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from clipoteca.studio import related
from clipoteca.studio.index import DryWriter, index_clips
from clipoteca.studio.related_cache import (
    cache_key, load_cache, make_entry, merge_entries, save_cache,
)


def _entry():
    return make_entry(("chiloe", (), True, None), {}, {
        "terms": [{"term": "Castro", "origin": "commons"}],
        "commons_roots": ["Category:Chiloe"], "geographic": True,
    })


def test_fresh_cache_avoids_network_and_stale_survives_failure(tmp_path):
    entry = _entry()
    assert entry
    path = tmp_path / "related_terms.json"
    save_cache(path, merge_entries({}, [entry]))

    async def run():
        calls = []

        def response(request):
            calls.append(request)
            return httpx.Response(503, headers={"Retry-After": "0"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            job = {"queries": {"en": ["Chiloe"], "es": []}}
            await related.resolve_jobs([job], client=client, persistent_cache=load_cache(path))
            assert not calls
            assert job["related_diagnostic"]["cache_hits"] == ["chiloe"]
            assert job["related_terms"] == entry["terms"]
            assert not job["related_cache_entries"]
            entry["date"] = (datetime.now(UTC) - timedelta(days=31)).isoformat()
            cache = {cache_key(("chiloe", (), True, None)): entry}
            await related.resolve_jobs([job], client=client, persistent_cache=cache,
                                       timeout=0.1, batch_timeout=0.3)
            assert calls
            assert job["related_diagnostic"]["cache_stale"] == ["chiloe"]
            assert job["related_terms"] == entry["terms"]
            assert not job["related_cache_entries"]

    asyncio.run(run())


def test_invalid_cache_rows_are_not_written(tmp_path):
    entry = _entry()
    bad = {**entry, "terms": [{"term": "bad\u0000term", "origin": "commons"}]}
    long = {**entry, "terms": [{"term": "x" * 81, "origin": "commons"}]}
    assert merge_entries({}, [bad, long]) == {}
    path = tmp_path / "related_terms.json"
    save_cache(path, merge_entries({}, [bad, entry, long]))
    assert list(load_cache(path).values()) == [entry]


def test_index_merges_scene_cache_without_imported_clips(tmp_path):
    from clipoteca.catalog import Catalog

    work = tmp_path / "work"
    scene = work / "scenes" / "one"
    scene.mkdir(parents=True)
    entry = _entry()
    (scene / "job.json").write_text(json.dumps({"related_cache_entries": [entry]}))
    dry = tmp_path / "dry"
    writer = DryWriter(dry, Catalog([]))
    result = index_clips(work, repository="", token="", writer=writer)
    assert not result.get("errors")
    assert list(load_cache(dry / "data" / "related_terms.json").values()) == [entry]


def test_bundle_uses_single_non_forced_ref_update():
    import base64

    from clipoteca.catalog import Catalog, GitHubCatalogWriter

    calls = []

    def response(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, path, body))
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "parent"}})
        if path.endswith("/contents/data/related_terms.json"):
            assert request.url.params["ref"] == "parent"
            return httpx.Response(200, json={"content": base64.b64encode(
                b'{"version":1,"entries":[]}'
            ).decode()})
        if path.endswith("/git/commits/parent"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if path.endswith("/git/trees"):
            assert {row["path"] for row in body["tree"]} == {
                "data/clips.json", "data/related_terms.json"
            }
            return httpx.Response(201, json={"sha": "tree"})
        if path.endswith("/git/commits"):
            assert body["parents"] == ["parent"]
            return httpx.Response(201, json={"sha": "commit"})
        if path.endswith("/git/refs/heads/main"):
            assert body == {"sha": "commit", "force": False}
            return httpx.Response(200, json={})
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        writer = GitHubCatalogWriter("example/repo", "", client=client)
        assert writer._publish_bundle(writer._bundle_head(), [_entry()], Catalog([])) == "commit"
    assert sum(method == "PATCH" for method, _, _ in calls) == 1
