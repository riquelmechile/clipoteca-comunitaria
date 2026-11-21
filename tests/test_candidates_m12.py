"""Offline M12 tests for declarative capabilities and candidate parsers."""

from __future__ import annotations

import json

import httpx
import pytest

from clipoteca.source_snapshot import snapshot
from clipoteca.sources import SOURCES, SOURCE_CAPABILITIES, list_sources
from scripts.candidate_probe import parse_metrics, first_rutube_id, sign_wbi, probe_candidate
from scripts.render_candidates_report import render


def test_capabilities_single_declaration_no_secret_leak():
    rows = list_sources(env={})
    assert len(SOURCE_CAPABILITIES) == len(SOURCES) == len(rows) == 15
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
        result = await probe_candidate(client, "tiktok", "zh")
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
