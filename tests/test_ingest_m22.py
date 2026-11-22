"""M22 full ingest optional embeddings sidecars share one remote client and close."""

import asyncio
import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from clipoteca.catalog import GitHubCatalogWriter
from clipoteca.ingest import ingest
from test_embeddings_m21 import FakeEncoder, scene_embeddings
from test_ingest_m4c import FakeStorage, FixtureAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "multiple", "cancel"])
async def test_persistent_ingest_reuses_one_embedding_client_for_queries_and_shards(
    tmp_path,
    monkeypatch,
    mode,
):
    catalogue = []
    generation = 0
    opened = []
    operations = []
    batch_sizes = []
    downloads = 0

    def github(req):
        nonlocal generation, catalogue
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sha": f"version-{generation}",
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(catalogue).encode()).decode(),
                },
            )
        payload = json.loads(req.content)
        assert payload["sha"] == f"version-{generation}"
        catalogue = json.loads(base64.b64decode(payload["content"]))
        generation += 1
        return httpx.Response(200, json={"commit": {"sha": f"commit-{generation}"}})

    writer = GitHubCatalogWriter(
        "riquelmechile/clipoteca-comunitaria",
        "test",
        client=httpx.Client(transport=httpx.MockTransport(github)),
    )

    class RemoteJobs:
        def __init__(self, *args, **kwargs):
            self.records = {}

        def read(self, identifier):
            record = self.records.get(identifier)
            return (record, "sha" if record else None)

        def save(self, job):
            self.records[job["request_id"]] = json.loads(json.dumps(job))

    class EmbeddingsRemote:
        def __init__(self, *_args, **_kwargs):
            self.client = object()
            self.closed = False
            opened.append(self)

        def publish(self, clip_id, vector):
            operations.append(("legacy", id(self.client), clip_id))
            assert vector.shape == (512,)

        def close(self):
            self.closed = True

    class ShardsRemote:
        def __init__(self, client, *_args, **_kwargs):
            self.client = client

        def publish_many(self, rows):
            batch_sizes.append(len(rows))
            for clip_id, vector in rows:
                operations.append(("shard", id(self.client), clip_id))

    class QueriesRemote:
        def __init__(self, client, *_args, **_kwargs):
            self.client = client

        def publish(self, query, vector):
            operations.append(("query", id(self.client), query))

    monkeypatch.setenv("CLIPOTECA_PERSIST_JOBS", "1")
    monkeypatch.setattr("clipoteca.ingest.GitHubJobWriter", RemoteJobs)
    monkeypatch.setattr("clipoteca.ingest.GitHubEmbeddingWriter", EmbeddingsRemote)
    monkeypatch.setattr("clipoteca.ingest.GitHubShards", ShardsRemote)
    monkeypatch.setattr("clipoteca.ingest.GitHubQueries", QueriesRemote)
    monkeypatch.setattr(
        "clipoteca.ingest.extract_frame_embeddings", lambda *_args: scene_embeddings()
    )

    async def downloader(_url: str, target: Path) -> Path:
        nonlocal downloads
        downloads += 1
        if mode == "cancel" and downloads == 2:
            raise asyncio.CancelledError()
        source = target / "input.mp4"
        size = "320x180" if "pexels" in _url else "240x240"
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
                f"testsrc2=size={size}:rate=12:duration=0.5",
                "-c:v",
                "libx264",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return source

    async def run():
        return await ingest(
            "rain",
            [FixtureAdapter("pexels")]
            if mode == "single"
            else [FixtureAdapter("pexels"), FixtureAdapter("pixabay")],
            repository="riquelmechile/clipoteca-comunitaria",
            token="test",
            writer=writer,
            storage=FakeStorage(),
            downloader=downloader,
            job_root=tmp_path / "jobs",
            max_results=1 if mode == "single" else 2,
            max_clips_per_source=1,
            embedding_encoder=FakeEncoder(),
            deferred_sources=("bilibili",) if mode == "single" else (),
            translator=lambda _text, _src, dst: {
                "es": "Lluvia",
                "en": "Rain",
                "zh": "\u96e8",
                "ru": "\u0414\u043e\u0436\u0434\u044c",
            }[dst],
        )

    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await run()
        assert len(catalogue) == 1
    else:
        report = await run()
        assert report.status == "published", report.errors
        if mode == "single":
            assert "bilibili:deferred_to_self_hosted" in report.errors
            job = json.loads((tmp_path / "jobs" / (report.request_id + ".json")).read_text())
            assert job["source_errors"]["bilibili"] == "deferred_to_self_hosted"
        assert len(catalogue) == (2 if mode == "multiple" else 1)
    assert batch_sizes == [len(catalogue)]
    assert len(opened) == 1
    assert opened[0].closed
    assert {op for op, _, _ in operations} == {"query", "legacy", "shard"}
    assert len({client_id for _, client_id, _ in operations}) == 1
    query_files = list((tmp_path / "embeddings" / "queries").glob("*.json"))
    assert len(query_files) == 1
    manifest = json.loads((tmp_path / "embeddings" / "manifest.json").read_text())
    assert manifest["shards"] == []
    assert manifest["tail"]["count"] == len(catalogue)
    ids = json.loads((tmp_path / "embeddings" / manifest["tail"]["ids_path"]).read_text())
    assert set(ids) == {clip["id"] for clip in catalogue}
