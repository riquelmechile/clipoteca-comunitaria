"""Offline M22 shards, migration, query vector cache and CAS tests."""

import base64
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from clipoteca.media.embedding_store import EmbeddingStore, EmbeddingError
from clipoteca.media.shards import (
    GitHubQueries,
    GitHubShards,
    QueryStore,
    ShardStore,
    SHARD_CAPACITY,
    MAX_SHARD_BYTES,
    fresh_manifest,
    git_blob_sha,
    migrate_legacy,
    normalized_query,
    query_digest,
    validate_manifest,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/m22/semantic_vectors.json").read_text())


class FakeEncoder:
    def encode_text(self, query):
        if normalized_query(query) not in {"aguacero", "lluvia", "rain", "下雨", "дождь"}:
            raise ValueError("unknown fake query")
        v = np.zeros(512, dtype=np.float32)
        v[0] = 1
        v[1] = 0.02
        return v / np.linalg.norm(v)


def fake_vector(axis):
    v = np.zeros(512, dtype=np.float32)
    v[axis] = 1
    return v


def test_shards_binary_concat_read_and_exact_cross_language_fixture(tmp_path):
    store = ShardStore(tmp_path)
    store.put("clip-000101", fake_vector(0))
    store.put("clip-000102", fake_vector(1))
    manifest = store.manifest()
    assert manifest == FIXTURE["manifest"]
    shard = manifest["shards"][0]
    assert shard["count"] == 2
    assert len((tmp_path / shard["path"]).read_bytes()) == 2048
    assert (tmp_path / shard["path"]).read_bytes() == base64.b64decode(FIXTURE["shard_base64"])
    assert store.read("clip-000101")[0] == pytest.approx(1)
    assert store.read("clip-000102")[1] == pytest.approx(1)
    assert SHARD_CAPACITY == 4096
    assert MAX_SHARD_BYTES == SHARD_CAPACITY * 1024


def test_legacy_to_shards_migration_idempotent_and_transition_reader(tmp_path):
    legacy = EmbeddingStore(tmp_path)
    legacy.put("clip-000101", fake_vector(0))
    legacy.put("clip-000102", fake_vector(1))
    # Simulate M21 pre-M22 state, with only per-clip sidecars and index.
    (tmp_path / "manifest.json").unlink(missing_ok=True)
    for path in (tmp_path / "shards").glob("*.f16"):
        path.unlink()
    transitional = ShardStore(tmp_path)
    assert transitional.read("clip-000101")[0] == pytest.approx(1)
    migrated = migrate_legacy(tmp_path)
    assert len(migrated["shards"]) == 1
    assert migrated["shards"][0]["count"] == 2
    assert migrated == migrate_legacy(tmp_path)
    assert transitional.read("clip-000102")[1] == pytest.approx(1)


def test_manifest_rejects_duplicate_ids_and_forged_shard_path():
    bad = json.loads(json.dumps(FIXTURE["manifest"]))
    bad["shards"][0]["ids"][1] = bad["shards"][0]["ids"][0]
    with pytest.raises(EmbeddingError, match="duplicate"):
        validate_manifest(bad)
    bad = json.loads(json.dumps(FIXTURE["manifest"]))
    bad["shards"][0]["path"] = "../secret"
    with pytest.raises(EmbeddingError, match="shard_invalid"):
        validate_manifest(bad)
    assert fresh_manifest()["shards"] == []


def test_query_cache_normalizes_and_records_only_sha_not_raw_term(tmp_path):
    cache = QueryStore(tmp_path)
    fake = FakeEncoder()
    path = cache.put("  AGUACERO   ", fake.encode_text("aguacero"))
    assert path == cache.put("aguacero", fake.encode_text("aguacero"))
    assert path.name == query_digest("aguacero") + ".json"
    assert "AGUACERO" not in path.read_text()
    assert cache.read("AGUACERO")[0] == pytest.approx(0.9998, abs=0.001)
    assert query_digest("  Aguacero  ") == query_digest("aguacero")
    assert query_digest("下雨") == FIXTURE["queries"]["下雨"]["query_sha256"]


def test_remote_shards_conflict_409_retries_latest_manifest_and_blob_api():
    files: dict[str, bytes] = {}
    blobs: dict[str, bytes] = {}
    attempts = 0
    blob_reads = 0

    def handle(req):
        nonlocal attempts, blob_reads
        url = str(req.url)
        if "/git/blobs/" in url:
            blob_reads += 1
            sha = url.rsplit("/", 1)[-1]
            if sha not in blobs:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(blobs[sha]).decode(),
                },
            )
        name = url.split("/data/embeddings/")[-1].split("?")[0]
        if req.method == "GET":
            if name not in files:
                return httpx.Response(404)
            data = files[name]
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(data).decode(),
                    "sha": git_blob_sha(data),
                },
            )
        assert req.method == "PUT"
        body = json.loads(req.content)
        payload = base64.b64decode(body["content"])
        if name == "manifest.json":
            attempts += 1
            if attempts == 1:
                # Concurrent CAS conflict: caller must reread then retry.
                return httpx.Response(409)
            expected = git_blob_sha(files[name]) if name in files else None
            if body.get("sha") != expected:
                return httpx.Response(409)
        files[name] = payload
        blobs[git_blob_sha(payload)] = payload
        return httpx.Response(201, json={"content": {"sha": git_blob_sha(payload)}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        writer = GitHubShards(client, "riquelmechile/clipoteca-comunitaria")
        writer.publish("clip-000101", fake_vector(0))
        writer.publish("clip-000102", fake_vector(1))
        writer.publish("clip-000102", fake_vector(1))
        with pytest.raises(EmbeddingError, match="immutable"):
            writer.publish("clip-000102", fake_vector(0))

    assert attempts >= 3
    manifest = json.loads(files["manifest.json"])
    assert manifest["shards"][0]["count"] == 2
    assert blob_reads > 0
    assert files[manifest["shards"][0]["path"]] == base64.b64decode(FIXTURE["shard_base64"])


def test_remote_query_cache_idempotent_409_detects_collision():
    files: dict[str, bytes] = {}
    calls = 0

    def handle(req):
        nonlocal calls
        calls += 1
        path = str(req.url).split("/data/embeddings/")[-1].split("?")[0]
        if req.method == "GET":
            if path not in files:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(files[path]).decode(),
                    "sha": git_blob_sha(files[path]),
                },
            )
        files[path] = base64.b64decode(json.loads(req.content)["content"])
        return httpx.Response(201)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        remote = GitHubQueries(client, "riquelmechile/clipoteca-comunitaria")
        remote.publish("Aguacero", FakeEncoder().encode_text("aguacero"))
        remote.publish("aguacero", FakeEncoder().encode_text("aguacero"))
    assert len(files) == 1 and calls == 3
    assert "aguacero" not in next(iter(files))


def test_query_fixture_is_python_little_endian_512():
    from clipoteca.media.embedding_store import decode_vector

    for term, record in FIXTURE["queries"].items():
        assert record["query_sha256"] == query_digest(term)
        value = decode_vector(base64.b64decode(record["vector"]))
        assert value.shape == (512,)
        assert float(np.dot(value, fake_vector(0))) > 0.99


def test_search_schema_accepts_optional_semantic_status_and_fusion_scores():
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).parents[1] / "schemas/search.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(
        {
            "clips": [],
            "next_cursor": None,
            "semantic": "ready",
            "scores": {"clip-000101": 1 / 61},
        }
    )
    validator.validate({"clips": [], "next_cursor": None, "semantic": "pending"})
    validator.validate({"clips": [], "next_cursor": None})
    with pytest.raises(Exception):
        validator.validate(
            {
                "clips": [],
                "next_cursor": None,
                "semantic": "done",
            }
        )
