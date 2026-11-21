"""M21 r2 resilience and complete ingestion sidecar regressions, no network."""

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from clipoteca.catalog import GitHubCatalogWriter
from clipoteca.ingest import _candidate_windows, _embedding_result, ingest
from clipoteca.media import select
from clipoteca.media.embedding_store import EmbeddingError, GitHubEmbeddingWriter
from clipoteca.media.scenes import Scene
from test_embeddings_m21 import FakeEncoder, scene_embeddings
from test_ingest_m4c import FakeStorage, FixtureAdapter


def test_semantic_cosine_cannot_override_m14_black_title_gate(monkeypatch):
    monkeypatch.setattr(
        select, "probe_media", lambda *_args, **_kwargs: SimpleNamespace(duration_seconds=40)
    )
    monkeypatch.setattr(select, "_sample_scores", lambda *_args, **_kwargs: [0.0] * 20 + [0.7] * 20)
    monkeypatch.setattr(
        select, "detect_scenes", lambda *_args, **_kwargs: [Scene(0, 20), Scene(20, 40)]
    )
    windows = select.rank_scene_windows(
        "source.mp4",
        max_clips=1,
        semantic_scorer=lambda window: 0.999 if window.end <= 20 else 0.1,
    )
    assert len(windows) == 1
    assert windows[0].start > 0
    assert windows[0].end > 20


def test_encoder_failure_is_cached_per_process_and_falls_back(monkeypatch):
    attempts = 0

    def model_load():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("model initialization unavailable")

    monkeypatch.setattr("clipoteca.ingest.load_encoder", model_load)
    monkeypatch.setattr(
        "clipoteca.ingest.rank_scene_windows",
        lambda *_args, **_kw: [select.SourceWindow(0, 10)],
    )
    statuses = []
    for index in range(3):
        job = {}
        assert _candidate_windows(
            Path("local-source.mp4"),
            duration_limit=20,
            max_clips=1,
            job=job,
            origin_id=f"test:{index}",
            query="rain",
        )
        statuses.append(_embedding_result(job))
    assert attempts == 1
    assert statuses == ["embeddings_extraction_failed:m14_quality_fallback"] * 3


def test_timeout_does_not_overwrite_embedding_warning_and_worst_status(monkeypatch):
    def model_load():
        raise RuntimeError("no weights")

    monkeypatch.setattr("clipoteca.ingest.load_encoder", model_load)

    def timeout(*_args, **_kwargs):
        raise select.SceneSelectionTimeout("bounded sampling")

    monkeypatch.setattr("clipoteca.ingest.rank_scene_windows", timeout)
    monkeypatch.setattr(
        "clipoteca.ingest.probe_media",
        lambda *_a, **_k: SimpleNamespace(duration_seconds=22),
    )
    monkeypatch.setattr(
        "clipoteca.ingest.fallback_quality_window",
        lambda *_a, **_k: select.SourceWindow(0, 12),
    )
    job = {}
    assert _candidate_windows(
        Path("local-source.mp4"),
        duration_limit=20,
        max_clips=1,
        job=job,
        origin_id="test:1",
        query="rain",
    )
    assert job["source_warnings"]["test:1"] == ("embeddings_extraction_failed:m14_quality_fallback")
    assert job["source_warnings"]["test:1:scene_selection"].startswith("scene_selection_timeout:")
    job["source_warnings"]["test:2"] = "embeddings_ranked"
    assert _embedding_result(job) == "embeddings_extraction_failed:m14_quality_fallback"
    job["source_warnings"]["test:3"] = "embeddings_persist_failed:clip_published"
    assert _embedding_result(job) == "embeddings_persist_failed:clip_published"


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_fails", [False, True])
async def test_complete_ingest_publishes_clip_and_calls_embedding_put_even_if_failing(
    persist_fails, tmp_path, monkeypatch
):
    published = []
    generation = 0

    def github(req):
        nonlocal published, generation
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sha": f"rev-{generation}",
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(published).encode()).decode(),
                },
            )
        obj = json.loads(req.content)
        assert obj["sha"] == f"rev-{generation}"
        published = json.loads(base64.b64decode(obj["content"]))
        generation += 1
        return httpx.Response(200, json={"commit": {"sha": f"published-{generation}"}})

    writer = GitHubCatalogWriter(
        "riquelmechile/clipoteca-comunitaria",
        "test",
        client=httpx.Client(transport=httpx.MockTransport(github)),
    )

    async def download(_url, dest):
        source = dest / "input.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=12:duration=0.5",
                "-c:v",
                "libx264",
                str(source),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return source

    samples = scene_embeddings()
    monkeypatch.setattr("clipoteca.ingest.extract_frame_embeddings", lambda *_a: samples)
    monkeypatch.delenv("CLIPOTECA_PERSIST_JOBS", raising=False)
    calls = []

    def put(self, clip_id, vector):
        calls.append((clip_id, float(np.linalg.norm(vector))))
        if persist_fails:
            raise RuntimeError("simulated persistent store failure")
        return {"path": f"{clip_id}.f16", "sha256": "a" * 64}

    monkeypatch.setattr("clipoteca.ingest.EmbeddingStore.put", put)
    report = await ingest(
        "rain",
        [FixtureAdapter("pexels")],
        repository="riquelmechile/clipoteca-comunitaria",
        token="test",
        writer=writer,
        storage=FakeStorage(),
        downloader=download,
        job_root=tmp_path / "jobs",
        max_results=1,
        max_clips_per_source=1,
        embedding_encoder=FakeEncoder(),
        translator=lambda _text, _src, dst: {
            "es": "Lluvia",
            "en": "Rain",
            "zh": "雨",
            "ru": "Дождь",
        }[dst],
    )
    assert report.status == "published", report.errors
    assert len(report.published_ids) == len(published) == 1
    assert len(calls) == 1
    assert calls[0][0] == report.published_ids[0]
    assert calls[0][1] == pytest.approx(1)
    assert report.embedding_status == (
        "embeddings_persist_failed:clip_published" if persist_fails else "embeddings_ranked"
    )


def test_remote_embedding_index_409_conflict_retries_and_closes_only_owned_client():
    files = {}
    index_puts = 0

    def handler(req):
        nonlocal index_puts
        name = req.url.path.rsplit("/", 1)[-1]
        if req.method == "GET":
            if name not in files:
                return httpx.Response(404)
            payload = files[name]
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(payload).decode(),
                    "sha": hashlib.sha1(payload).hexdigest(),
                },
            )
        body = json.loads(req.content)
        if name == "index.json":
            index_puts += 1
            if index_puts == 1:
                return httpx.Response(409)
        files[name] = base64.b64decode(body["content"])
        return httpx.Response(201)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    writer = GitHubEmbeddingWriter("riquelmechile/clipoteca-comunitaria", "test", client=client)
    vector = np.zeros(512, dtype=np.float32)
    vector[0] = 1
    writer.publish("clip-000201", vector)
    assert index_puts == 2
    assert json.loads(files["index.json"])["vectors"]["clip-000201"]["path"] == "clip-000201.f16"
    writer.close()
    assert not client.is_closed
    client.close()


def test_remote_embedding_missing_sha_is_typed_error_not_keyerror():
    def handler(req):
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": base64.b64encode(b"ok").decode()},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        writer = GitHubEmbeddingWriter("riquelmechile/clipoteca-comunitaria", "test", client=client)
        with pytest.raises(EmbeddingError, match="sha_missing"):
            writer._read("index.json")
