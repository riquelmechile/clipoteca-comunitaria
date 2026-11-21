"""Offline M12 tests for declarative capabilities and candidate parsers."""

from __future__ import annotations

import json

import httpx
import pytest

from clipoteca.source_snapshot import snapshot
from clipoteca.sources import SOURCES, SOURCE_CAPABILITIES, list_sources
from clipoteca.candidate_probe import parse_metrics, first_rutube_id, sign_wbi, probe_candidate
from clipoteca.candidates_report import render
from clipoteca.config import USER_AGENT


def test_capabilities_single_declaration_no_secret_leak():
    rows = list_sources(env={})
    assert len(SOURCE_CAPABILITIES) == len(SOURCES) == len(rows) == 21
    assert {r["name"] for r in rows} == set(SOURCE_CAPABILITIES)
    for row in rows:
        assert row["capabilities"] == SOURCE_CAPABILITIES[row["name"]]
        assert set(row["capabilities"]) == {"search", "preview", "download"}
        assert set(row["capabilities"].values()) <= {"supported", "best_effort", "unavailable"}
    assert {r["name"]: r["capabilities"] for r in snapshot()} == SOURCE_CAPABILITIES
    assert "SECRET" not in json.dumps(list_sources(env={"PEXELS_API_KEY": "SECRET"}))


@pytest.mark.parametrize(
    ("provider", "payload", "count", "stream", "code"),
    [
        ("rutube", {"results": [{"id": "abc"}]}, 1, False, None),
        ("rutube", {"video_balancer": {"hls": "https://test.invalid"}}, 0, True, None),
        ("vk_video", {"response": {"items": [{"files": {"hls": "x"}}]}}, 1, True, None),
        ("library_of_congress", {"results": [{"resources": [{"video": ["x"]}]}]}, 1, True, None),
        ("douyin", {"status_code": 2483, "search_nil_info": {"reason": "session"}}, 0, False, 2483),
        ("tiktok", {"item_list": [{"video": {"playAddr": "x"}}]}, 1, True, None),
        ("bilibili", {"code": -412, "data": {}}, 0, False, -412),
    ],
)
def test_parse_candidates(provider, payload, count, stream, code):
    out = parse_metrics(provider, payload)
    assert out["count"] == count
    assert out["has_stream"] is stream
    assert out["platform_code"] == code


def test_ok_html_and_rutube_safe_identifier():
    assert parse_metrics("ok_ru", {}, html='<div data-props="a"></div>')["count"] == 1
    assert first_rutube_id({"results": [{"id": "../escape"}]}) is None
    assert first_rutube_id({"results": [{"id": "abc_123"}]}) == "abc_123"


def test_wbi_signature_repeatable():
    # Synthetic URLs, not a live credential.
    chars = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    params = sign_wbi(
        {"keyword": "rain"},
        f"https://i.example/{chars}.png",
        f"https://i.example/{chars}.png",
        1234567890,
    )
    assert len(params["w_rid"]) == 32
    assert params["wts"] == "1234567890"
    assert params == sign_wbi(
        {"keyword": "rain"},
        f"https://i.example/{chars}.png",
        f"https://i.example/{chars}.png",
        1234567890,
    )


@pytest.mark.asyncio
async def test_guest_probe_redacts_payload_and_handles_blocked_http():
    def handler(req: httpx.Request):
        return httpx.Response(403, json={"status_code": 2483, "access_token": "SECRET"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_candidate(client, "tiktok", "zh", resolver=lambda _host: ["8.8.8.8"])
    assert len(result) == 1
    assert result[0]["status_code"] == 403
    assert result[0]["platform_code"] == 2483
    assert "SECRET" not in json.dumps(result)
    assert "url" not in result[0]


def test_render_redacts_untrusted_payload():
    report = {
        "mode": "candidates",
        "provider_languages": [
            {
                "provider": "douyin",
                "language": "zh",
                "operation": "search",
                "status_code": 403,
                "platform_code": 2483,
                "elapsed_ms": 24,
                "count": 0,
                "has_stream": False,
                "access_token": "SECRET",
                "url": "https://secret.invalid",
            }
        ],
    }
    document = render(report)
    assert "2483" in document and "403" in document
    assert "SECRET" not in document and "secret.invalid" not in document


def PUBLIC_RESOLVER(_host):
    return ["8.8.8.8"]


def test_wbi_fixed_known_vector():
    """Fixed WBI vector: original order permutation + sorted URL-encoding + MD5."""
    result = sign_wbi(
        {"foo": "114", "bar": "514", "zab": "1919810"},
        "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
        "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        1702204169,
    )
    assert result["wts"] == "1702204169"
    assert result["w_rid"] == "8f6f2b5b3d485fe1886cec6a0be8c5d4"


@pytest.mark.asyncio
async def test_vk_guest_token_then_authenticated_search_never_report_token():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"] == USER_AGENT
        if request.url.host == "login.vk.com":
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": "SECRET_VK_TOKEN"})
        assert request.url.host == "api.vkvideo.ru"
        assert request.url.params["access_token"] == "SECRET_VK_TOKEN"
        assert request.url.params["q"] == "rain"
        return httpx.Response(200, json={"response": {"items": [{"files": {"hls": "x"}}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await probe_candidate(client, "vk_video", "en", resolver=PUBLIC_RESOLVER)
    assert [x["operation"] for x in rows] == ["init", "search"]
    assert rows[1]["count"] == 1 and rows[1]["has_stream"] is True
    assert len(requests) == 2
    assert "SECRET_VK_TOKEN" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_bilibili_nav_sign_wbi_then_search(monkeypatch):
    monkeypatch.setattr("clipoteca.candidate_probe.time.time", lambda: 1702204169)
    requests = []
    img = "7cd084941338484aae1ad9425b84077c"
    sub = "4932caff0ff746eab6f01bf08b70ac45"

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"] == USER_AGENT
        if request.url.path.endswith("/nav"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "wbi_img": {
                            "img_url": f"https://i0.hdslb.com/bfs/wbi/{img}.png",
                            "sub_url": f"https://i0.hdslb.com/bfs/wbi/{sub}.png",
                        }
                    }
                },
            )
        assert request.url.path.endswith("/wbi/search/type")
        assert request.url.params["wts"] == "1702204169"
        assert (
            request.url.params["w_rid"]
            == sign_wbi(
                {"search_type": "video", "keyword": "lluvia"},
                f"https://i0.hdslb.com/bfs/wbi/{img}.png",
                f"https://i0.hdslb.com/bfs/wbi/{sub}.png",
                1702204169,
            )["w_rid"]
        )
        return httpx.Response(200, json={"code": 0, "data": {"result": [{"bvid": "BV1"}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await probe_candidate(client, "bilibili", "es", resolver=PUBLIC_RESOLVER)
    assert len(requests) == 2
    assert [r["operation"] for r in rows] == ["init", "search"]
    assert rows[1]["count"] == 1 and rows[1]["platform_code"] == 0


@pytest.mark.asyncio
async def test_douyin_guest_ttwid_forwarded_cross_domain_only_in_memory():
    requests = []
    guest = "very_private_guest_cookie_123"

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"] == USER_AGENT
        if request.url.host == "ttwid.bytedance.com":
            assert request.method == "POST"
            return httpx.Response(
                200, json={"code": 0}, headers={"Set-Cookie": f"ttwid={guest}; Path=/; HttpOnly"}
            )
        assert request.url.host == "www.douyin.com"
        assert request.headers.get("cookie") == f"ttwid={guest}"
        return httpx.Response(
            200, json={"status_code": 0, "data": [{"aweme_info": {"video": {"play_addr": {}}}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await probe_candidate(client, "douyin", "zh", resolver=PUBLIC_RESOLVER)
    assert len(requests) == 2
    assert rows[1]["count"] == 1
    assert guest not in json.dumps(rows)
    assert "cookie" not in json.dumps(rows).lower()


@pytest.mark.asyncio
async def test_rutube_search_then_play_options_with_user_agent():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["user-agent"] == USER_AGENT
        if request.url.path == "/api/search/video/":
            assert request.url.params["query"] == "дождь"
            return httpx.Response(200, json={"results": [{"id": "safe_test_id"}]})
        assert request.url.path == "/api/play/options/safe_test_id/"
        assert request.headers.get("referer") == "https://rutube.ru/video/safe_test_id/"
        return httpx.Response(200, json={"video_balancer": {"hls": "redacted"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await probe_candidate(client, "rutube", "ru", resolver=PUBLIC_RESOLVER)
    assert len(requests) == 2
    assert rows[0]["count"] == 1
    assert rows[1]["has_stream"] is True


@pytest.mark.asyncio
async def test_dns_private_ip_rejected_before_network():
    def handler(_request):
        pytest.fail("private DNS must be rejected before sending a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await probe_candidate(client, "tiktok", "es", resolver=lambda _host: ["127.0.0.1"])
    assert rows[0]["status_code"] is None
    assert rows[0]["error_type"] == "UnsafeMediaURL"


def test_candidate_report_displays_error_type_and_search_nil_info():
    markdown = render(
        {
            "mode": "candidates",
            "provider_languages": [
                {
                    "provider": "douyin",
                    "language": "zh",
                    "operation": "search",
                    "status_code": 403,
                    "search_nil_info": True,
                    "error_type": "TimeoutException",
                },
                {
                    "provider": "rutube",
                    "language": "en",
                    "operation": "search",
                    "status_code": None,
                },
            ],
        }
    )
    assert "search_nil_info" in markdown
    assert "TimeoutException" in markdown
    assert "NO_HTTP_RESPONSE" in markdown
    assert " | sí | " in markdown


def test_smoke_no_mode_runs_both_report_families(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke_sources.py"
    spec = importlib.util.spec_from_file_location("clipoteca_sources_smoke", script)
    assert spec is not None and spec.loader is not None
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    provider = {
        "mode": "live_network_probes",
        "provider_languages": [],
        "total_probes": 1,
        "elapsed_seconds_measured": 0.1,
    }
    candidate = {
        "mode": "candidates",
        "provider_languages": [{"provider": "douyin", "language": "es", "operation": "search"}],
        "total_probes": 1,
        "elapsed_seconds_measured": 0.2,
    }

    async def fake_providers():
        return provider.copy()

    async def fake_candidates():
        return candidate.copy()

    monkeypatch.setattr(smoke, "main_async", fake_providers)
    monkeypatch.setattr(smoke, "candidates_smoke", fake_candidates)
    target = tmp_path / "smoke.json"
    assert smoke.main(["--output", str(target)]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["mode"] == "live_network_and_candidate_probes"
    assert data["total_probes"] == 1 and data["total_candidate_probes"] == 1
    assert data["candidates"]["provider_languages"][0]["provider"] == "douyin"
    assert smoke.main(["--mode", "providers", "--output", str(target)]) == 0
    assert "candidates" not in json.loads(target.read_text(encoding="utf-8"))
