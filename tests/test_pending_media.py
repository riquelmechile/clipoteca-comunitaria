"""Pending contributions use one bounded registration wait before catalogue fallback."""

import asyncio
import json

import httpx
import pytest

from clipoteca.studio.pending_media import resolve_pending_jobs
from clipoteca.studio.script import ScriptError
from clipoteca.studio.selection import select_scene

REF = "a2026-11-" + "a" * 32
API = "https://clipoteca.test"


def job(with_query=True):
    return {
        "id": "fact",
        "source_kind": "contribution",
        "media": {"ref": REF},
        "queries": {"es": ["agua"], "en": ["water"]} if with_query else {},
        "prompt": "water" if with_query else "",
    }


def rejected(request):
    assert request.url.path == "/v1/media/" + REF
    return httpx.Response(
        200, json={"media_id": REF, "status": "rejected", "reason": "invalid_file"}
    )


def test_rejected_falls_back_to_catalogue_with_warning(tmp_path):
    scene = job()
    estimate = {"id": "fact", "source_kind": "contribution"}
    asyncio.run(
        resolve_pending_jobs(
            [scene], [estimate], API, transport=httpx.MockTransport(rejected)
        )
    )
    assert "media" not in scene
    assert "source_kind" not in estimate
    assert REF in scene["media_fallback_reason"]
    assert "invalid_file" in scene["media_fallback_reason"]
    (tmp_path / "job.json").write_text(json.dumps(scene))
    (tmp_path / "assignment.json").write_text(json.dumps({"status": "matched"}))

    def runner(job, assignment, out, **kwargs):
        out.mkdir()
        result = {"status": "ok", "catalog_strong": True, "clip": {"sim": 0.5, "score": 1}}
        (out / "clip.mp4").write_bytes(b"catalogue")
        (out / "result.json").write_text(json.dumps(result))
        return result

    result = select_scene(tmp_path, "catalog", runner=runner)
    assert result["source"] == "catalog"
    assert result["fallback"] is True
    assert result["degraded_reason"] == scene["media_fallback_reason"]


def test_rejected_without_query_has_clear_error():
    with pytest.raises(ScriptError, match=REF + ".*invalid_file"):
        asyncio.run(
            resolve_pending_jobs(
                [job(False)], [{"id": "fact"}], API, transport=httpx.MockTransport(rejected)
            )
        )


def test_processing_then_registered_preserves_reference():
    count = 0

    def response(request):
        nonlocal count
        count += 1
        return httpx.Response(
            200, json={"media_id": REF, "status": "registered" if count > 1 else "processing"}
        )

    scene = job()
    asyncio.run(
        resolve_pending_jobs(
            [scene], [{"id": "fact"}], API,
            poll_seconds=0, transport=httpx.MockTransport(response),
        )
    )
    assert count == 2
    assert scene["media"]["ref"] == REF


def test_timeout_fallback():
    scene = job()
    asyncio.run(
        resolve_pending_jobs([scene], [{"id": "fact"}], API, max_seconds=0)
    )
    assert "registration_timeout" in scene["media_fallback_reason"]
