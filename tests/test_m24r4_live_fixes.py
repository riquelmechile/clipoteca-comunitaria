"""M24 r4 offline regressions for observed provider protocol changes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from clipoteca.providers import vk_video
from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
)
from clipoteca.providers.bilibili import Bilibili
from clipoteca.providers.internet_archive import InternetArchive
from clipoteca.providers.library_of_congress import LibraryOfCongress
from clipoteca.providers.ok_ru import OkRu
from clipoteca.providers.rutube import Rutube

FIXTURES = Path(__file__).parent / "fixtures" / "m24r4"


@pytest.fixture
def smoke_module():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "smoke_sources.py"
    spec = importlib.util.spec_from_file_location("m24r4_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMG = "7cd084941338484aae1ad9425b84077c"
SUB = "4932caff0ff746eab6f01bf08b70ac45"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="ascii"))


@pytest.mark.asyncio
async def test_archive_accepts_real_qin_query_shape_without_weaker_mismatch():
    payload = fixture("archive_qin.json")

    def handler(req):
        assert req.url.path == "/advancedsearch.php"
        params = json.loads(json.dumps(payload))
        params["responseHeader"]["params"]["qin"] = req.url.params["q"]
        return httpx.Response(200, json=params)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        found = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert len(found) == 1 and found[0].provider_id == "verified_shape"


@pytest.mark.asyncio
async def test_archive_missing_echo_warns_and_accepts_only_explicit_movies():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, json=fixture("archive_missing_echo.json"))
        )
    ) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        with pytest.warns(RuntimeWarning, match="query echo absent"):
            found = await adapter.search("rain", "en", SearchLimits())
    assert [row.provider_id for row in found] == ["movie_1"]


@pytest.mark.asyncio
async def test_rutube_wdp_on_initial_and_paginated_search():
    queries = []

    def handler(req):
        queries.append(req.url.params)
        assert req.url.params["client"] == "wdp"
        if req.url.params["page"] == "1":
            payload = fixture("rutube_wdp.json")
            payload["next"] = "/api/search/video/?query=rain&page=2"
        else:
            payload = {"count": 0, "results": []}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await Rutube(client=client).search("rain", "en", SearchLimits(max_pages=2))
    assert len(rows) == 1
    assert [q["page"] for q in queries] == ["1", "2"]


@pytest.mark.asyncio
async def test_okru_direct_new_path_parses_trimmed_real_html():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        assert req.url.path == "/video/search/rain"
        return httpx.Response(
            200,
            text=(FIXTURES / "ok_ru_search_real_trimmed.html").read_text(encoding="ascii"),
            headers={"content-type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OkRu(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert len(rows) == 2
    assert rows[0].provider_id == "212995634"
    assert rows[0].source_title
    assert len(calls) == 1 and "st.gsq" not in calls[0]


@pytest.mark.asyncio
async def test_loc_retry_after_bounded_and_45_second_timeout(monkeypatch):
    from clipoteca.providers import library_of_congress as loc

    called = []
    delays = []
    original = loc.fetch_json
    import asyncio as aio

    async def fake_sleep(value):
        delays.append(value)

    async def spy(*args, **kwargs):
        called.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(aio, "sleep", fake_sleep)
    monkeypatch.setattr(loc, "fetch_json", spy)
    attempts = 0

    def handler(_req):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json=fixture("loc_search.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = LibraryOfCongress(client=client, sleep=fake_sleep)
        rows = await adapter.search("rain", "en", SearchLimits(max_pages=1, max_candidates=1))
    assert len(rows) == 1 and attempts == 2
    assert called[0]["timeout_seconds"] == 45.0
    assert called[0]["max_retry_after_seconds"] == 5.0
    assert 2.0 in delays


@pytest.mark.asyncio
async def test_bilibili_search_is_cookieless_and_retries_silent_empty_once(monkeypatch):
    nav = {
        "code": -101,
        "data": {
            "wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/" + IMG + ".png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/" + SUB + ".png",
            }
        },
    }
    calls = []

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    def handler(req):
        calls.append(req.url.path)
        assert "cookie" not in req.headers
        assert req.headers["origin"] == "https://www.bilibili.com"
        assert req.headers["user-agent"].startswith("Mozilla/5.0")
        assert not req.url.path.endswith("/finger/spi")
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=nav)
        if calls.count(req.url.path) == 1:
            return httpx.Response(200, json={"code": 0, "data": {"result": []}})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"result": [{"bvid": "BV1xx411c7mD", "title": "Rain"}], "numResults": 1},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await Bilibili(cookie="", client=client).search(
            "rain", "en", SearchLimits(max_pages=1)
        )
    assert [x.provider_id for x in rows] == ["BV1xx411c7mD"]
    assert calls.count("/x/web-interface/wbi/search/type") == 2


@pytest.mark.asyncio
async def test_bilibili_true_empty_numresults_zero_does_not_trigger_spi():
    nav = {
        "code": 0,
        "data": {
            "wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/" + IMG + ".png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/" + SUB + ".png",
            }
        },
    }

    def handler(req):
        if req.url.path.endswith("/nav"):
            return httpx.Response(200, json=nav)
        assert req.url.path.endswith("/search/type")
        return httpx.Response(200, json={"code": 0, "data": {"result": [], "numResults": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await Bilibili(cookie="", client=client).search("rain", "en", SearchLimits(max_pages=1))
            == []
        )


@pytest.mark.asyncio
async def test_vk_rate_limited_token_attempt_shared_across_languages(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", None)
    monkeypatch.setattr(vk_video, "_TOKEN_BACKOFF_UNTIL", 0.0)
    calls = 0

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    def handler(req):
        nonlocal calls
        calls += 1
        assert req.url.host == "login.vk.com"
        return httpx.Response(
            429, json=fixture("vk_rate_limited.json"), headers={"Retry-After": "2"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = vk_video.VKVideo(client=client, token="", clock=lambda: 100.0)
        second = vk_video.VKVideo(client=client, token="", clock=lambda: 100.0)
        with pytest.raises(RateLimited, match="anonymous login"):
            await first.search("rain", "en", SearchLimits(max_pages=1))
        initial = calls
        with pytest.raises(RateLimited, match="anonymous login"):
            await second.search("lluvia", "es", SearchLimits(max_pages=1))
    assert initial == calls == 3


@pytest.mark.asyncio
async def test_smoke_preserves_typed_messages_and_redacts_untrusted_data(smoke_module):
    smoke_sources = smoke_module

    assert fixture("douyin_session_required.json")["status_code"] == 2483
    assert fixture("tiktok_guest_init.json")["status_code"] == 1001
    rows = {}
    smoke_sources._diagnostic(rows, ProviderError("provider HTTP 403"))
    assert rows["error_detail"] == "provider HTTP 403"
    assert rows["http_status"] == 403
    rows = {}
    smoke_sources._diagnostic(rows, ProviderError("request https://example.com/SECRET"))
    assert rows["error_detail"] == "redacted"
    assert "SECRET" not in json.dumps(rows)
    rows = {}
    smoke_sources._diagnostic(rows, RuntimeError("Authorization: TOKEN"))
    assert "error_detail" not in rows

    class Fake:
        support_status = SupportStatus.SUPPORTED_SEARCH_ONLY

        async def search(self, *_args):
            raise AuthRequired("session_required")

    spec = SimpleNamespace(name="douyin", requires=(), adapter=Fake)
    outcome = await smoke_sources.probe(spec, "en")
    assert outcome["status"] == "SESSION_REQUIRED"
    assert outcome["error_detail"] == "session_required"


@pytest.mark.asyncio
async def test_smoke_serializes_four_languages_of_each_provider(monkeypatch, smoke_module):
    smoke_sources = smoke_module

    order = []

    class Fake:
        accepts_text_languages = ("es", "en", "zh", "ru")

    async def fake_probe(_spec, language):
        order.append(language)
        return {"provider": "one", "language": language, "count": 0}

    async def fast_sleep(_time):
        return None

    monkeypatch.setattr(smoke_sources, "SOURCES", (SimpleNamespace(name="one", adapter=Fake),))
    monkeypatch.setattr(smoke_sources, "probe", fake_probe)
    monkeypatch.setattr(smoke_sources, "github_usage", lambda: {})
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    report = await smoke_sources.main_async()
    assert order == ["es", "en", "zh", "ru"]
    assert report["total_probes"] == 4


@pytest.mark.asyncio
async def test_tiktok_init_1001_plus_empty_search_is_session_required():
    from clipoteca.providers.tiktok import TikTok

    calls = []

    def handler(req):
        calls.append(req.url.host)
        if req.url.host == "ttwid.bytedance.com":
            return httpx.Response(200, json=fixture("tiktok_guest_init.json"))
        return httpx.Response(200, json={"status_code": 0, "item_list": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTok(cookie="", client=client)
        with pytest.raises(AuthRequired, match="session_required"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert calls.count("www.tiktok.com") == 1


@pytest.mark.asyncio
async def test_candidate_probe_bilibili_cookieless_silent_block(monkeypatch):
    from clipoteca import candidate_probe as cp

    calls = []

    async def fake_request(_client, _method, url, **kwargs):
        headers = kwargs.get("headers") or {}
        calls.append((url, dict(headers)))
        assert not url.endswith("/finger/spi")
        if url.endswith("/nav"):
            return (
                200,
                {
                    "data": {
                        "wbi_img": {
                            "img_url": "https://i0.hdslb.com/bfs/wbi/" + IMG + ".png",
                            "sub_url": "https://i0.hdslb.com/bfs/wbi/" + SUB + ".png",
                        }
                    }
                },
                "",
                None,
            )
        assert url.endswith("/search/type")
        assert "Cookie" not in headers
        return 200, {"code": 0, "data": {"result": []}}, "", None

    monkeypatch.setattr(cp, "_request", fake_request)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: pytest.fail("no real network expected"))
    ) as client:
        rows = await cp.probe_candidate(client, "bilibili", "en")
    assert len(rows) == 2
    assert rows[-1]["diagnosis"] == "silent_block"
    assert rows[-1]["count"] == 0
    assert all("Cookie" not in json.dumps(row) for row in rows)
    assert all(url.startswith("https://api.bilibili.com/") for url, _ in calls)


@pytest.mark.asyncio
async def test_candidate_probe_douyin_2483_diagnosed_not_success(monkeypatch):
    from clipoteca import candidate_probe as cp

    async def fake_request(_client, _method, url, **_kwargs):
        if "ttwid.bytedance.com" in url:
            return 200, {"status_code": 0}, "", None
        return 200, fixture("douyin_session_required.json"), "", None

    monkeypatch.setattr(cp, "_request", fake_request)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _req: pytest.fail("no real network expected"))
    ) as client:
        rows = await cp.probe_candidate(client, "douyin", "es")
    assert rows[-1]["diagnosis"] == "session_required"
    assert rows[-1]["platform_code"] == 2483
