"""M22 r2 linear-size sealed shards, Git Data CAS and query cache."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import numpy as np
import pytest

from clipoteca.media.embedding_store import EmbeddingError, EmbeddingStore
from clipoteca.media.shards import (
    GitHubQueries,
    GitHubShards,
    QueryStore,
    ShardStore,
    MAX_SHARDS,
    MAX_SHARD_BYTES,
    SEAL_CAPACITY,
    SHARD_CAPACITY,
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


def vector(axis: int = 0) -> np.ndarray:
    array = np.zeros(512, dtype=np.float32)
    array[axis] = 1.0
    return array


def test_tail_binary_matches_python_fixture_and_reads_with_legacy_fallback(tmp_path):
    store = ShardStore(tmp_path)
    store.put_many([("clip-000101", vector(0)), ("clip-000102", vector(1))])
    manifest = store.manifest()
    assert manifest == FIXTURE["manifest"]
    assert manifest["version"] == 3 and manifest["shards"] == []
    assert manifest["tail"]["count"] == 2
    assert (tmp_path / "shards/tail.f16").read_bytes() == base64.b64decode(FIXTURE["tail_base64"])
    assert (tmp_path / "ids/tail.json").read_bytes() == base64.b64decode(FIXTURE["tail_ids_base64"])
    assert store.read("clip-000101")[0] == pytest.approx(1)
    assert store.read("clip-000102")[1] == pytest.approx(1)
    assert SHARD_CAPACITY == 4096
    assert MAX_SHARD_BYTES == 4096 * 1024
    assert SEAL_CAPACITY == 256
    assert MAX_SHARDS == 500


def test_migration_from_m21_sidecars_is_batch_and_idempotent(tmp_path):
    legacy = EmbeddingStore(tmp_path)
    legacy.put("clip-000101", vector(0))
    legacy.put("clip-000102", vector(1))
    # Recreate pre-M22 state: only M21 .f16 files + index.
    (tmp_path / "manifest.json").unlink(missing_ok=True)
    for folder in ("shards", "ids"):
        for f in (tmp_path / folder).glob("*"):
            f.unlink()
    assert ShardStore(tmp_path).read("clip-000101")[0] == pytest.approx(1)
    first = migrate_legacy(tmp_path)
    second = migrate_legacy(tmp_path)
    assert first == second
    assert first["tail"]["count"] == 2
    assert ShardStore(tmp_path).read("clip-000102")[1] == pytest.approx(1)


def test_manifest_without_ids_rejects_malformed_entries():
    broken = json.loads(json.dumps(FIXTURE["manifest"]))
    broken["tail"]["path"] = "../passwd"
    with pytest.raises(EmbeddingError, match="tail_invalid"):
        validate_manifest(broken)
    broken = json.loads(json.dumps(FIXTURE["manifest"]))
    broken["tail"]["sha256"] = "x" * 64
    with pytest.raises(EmbeddingError, match="shard_invalid"):
        validate_manifest(broken)
    assert fresh_manifest()["tail"] is None


def test_linear_growth_300_and_5000_inserts_without_orphans(tmp_path):
    store = ShardStore(tmp_path)
    rows = [(f"clip-{i:06d}", vector(i % 2)) for i in range(1, 5001)]
    store.put_many(rows[:300])
    m = store.manifest()
    assert len(m["shards"]) == 1 and m["tail"]["count"] == 44
    files300 = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert len(files300) == 5  # sealed bin + ids, fixed tail + ids, manifest
    assert sum(f.stat().st_size for f in files300) < 400_000
    store.put_many(rows[300:])
    m = store.manifest()
    assert len(m["shards"]) == 19 and m["tail"]["count"] == 136
    assert all("ids" not in sealed for sealed in m["shards"])
    assert len(m["shards"]) <= MAX_SHARDS
    files = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert len(files) == 19 * 2 + 2 + 1
    assert sum(f.stat().st_size for f in files) < 5_700_000
    assert store.read("clip-005000")[0] == pytest.approx(1, abs=1)
    assert store.put_many([rows[-1]]) == m


class GitDataSimulator:
    """Minimal repository Git object-store with a conditional fast-forward ref."""

    def __init__(self):
        self.head = "a" * 40
        self.blobs = {}
        self.trees = {"b" * 40: {}}
        self.commits = {self.head: {"tree": "b" * 40, "parent": None}}
        self.count = 0
        self.conflict_once = True
        self.commits_written = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = urlsplit(str(req.url)).path
        body = json.loads(req.content) if req.content else {}
        if req.method == "GET" and path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": self.head}})
        if req.method == "GET" and "/git/commits/" in path:
            ident = path.rsplit("/", 1)[-1]
            if ident not in self.commits:
                return httpx.Response(404)
            return httpx.Response(200, json={"tree": {"sha": self.commits[ident]["tree"]}})
        if req.method == "GET" and path.endswith("/contents/data/embeddings/manifest.json"):
            tree = self.trees[self.commits[self.head]["tree"]]
            payload = tree.get("data/embeddings/manifest.json")
            if payload is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(payload).decode(),
                    "sha": git_blob_sha(payload),
                },
            )
        if req.method == "GET" and "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[-1]
            if sha not in self.blobs:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(self.blobs[sha]).decode(),
                },
            )
        if req.method == "POST" and path.endswith("/git/blobs"):
            payload = base64.b64decode(body["content"])
            sha = git_blob_sha(payload)
            self.blobs[sha] = payload
            return httpx.Response(201, json={"sha": sha})
        if req.method == "POST" and path.endswith("/git/trees"):
            tree = dict(self.trees[body["base_tree"]])
            for item in body["tree"]:
                if item["sha"] is None:
                    tree.pop(item["path"], None)
                else:
                    tree[item["path"]] = self.blobs[item["sha"]]
            sha = hashlib.sha1(
                json.dumps(sorted((k, git_blob_sha(v)) for k, v in tree.items())).encode()
            ).hexdigest()
            self.trees[sha] = tree
            return httpx.Response(201, json={"sha": sha})
        if req.method == "POST" and path.endswith("/git/commits"):
            sha = hashlib.sha1(
                (body["tree"] + body["parents"][0] + str(self.commits_written)).encode()
            ).hexdigest()
            self.commits[sha] = {"tree": body["tree"], "parent": body["parents"][0]}
            self.commits_written += 1
            return httpx.Response(201, json={"sha": sha})
        if req.method == "PATCH" and path.endswith("/git/refs/heads/main"):
            self.count += 1
            if self.conflict_once:
                self.conflict_once = False
                return httpx.Response(409)
            new_head = body["sha"]
            if body["force"] or self.commits[new_head]["parent"] != self.head:
                return httpx.Response(409)
            self.head = new_head
            return httpx.Response(200, json={"object": {"sha": self.head}})
        return httpx.Response(404)


def test_remote_one_git_commit_per_batch_cas_and_no_orphans():
    remote = GitDataSimulator()
    rows = [(f"clip-{i:06d}", vector(i % 2)) for i in range(1, 301)]
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        publisher = GitHubShards(client, "riquelmechile/clipoteca-comunitaria")
        publisher.publish_many(rows)
        publisher.publish_many(rows)
        assert remote.count >= 2  # forced 409, then successful retry
        m = json.loads(
            remote.trees[remote.commits[remote.head]["tree"]]["data/embeddings/manifest.json"]
        )
        assert len(m["shards"]) == 1 and m["tail"]["count"] == 44
        assert len(remote.trees[remote.commits[remote.head]["tree"]]) == 5
        with pytest.raises(EmbeddingError, match="immutable"):
            publisher.publish("clip-000300", vector(0 if 300 % 2 else 1))


def test_remote_query_cache_idempotent():
    files = {}
    calls = 0

    def handler(req):
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

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        remote = GitHubQueries(client, "riquelmechile/clipoteca-comunitaria")
        remote.publish("Aguacero", FakeEncoder().encode_text("aguacero"))
        remote.publish("aguacero", FakeEncoder().encode_text("aguacero"))
    assert len(files) == 1 and calls == 3


def test_normalized_query_and_query_cache_with_fake_encoder(tmp_path):
    cache = QueryStore(tmp_path)
    path = cache.put("  AGUACERO  ", FakeEncoder().encode_text("aguacero"))
    assert path == cache.put("aguacero", FakeEncoder().encode_text("aguacero"))
    assert path.name == query_digest("aguacero") + ".json"
    assert "aguacero" not in path.read_text()
    assert cache.read("aguacero")[0] == pytest.approx(0.9998, abs=0.001)
    for row in json.loads((Path(__file__).parent / "fixtures/m22/normalization.json").read_text()):
        assert normalized_query(row["input"]) == row["normalized"]
        assert query_digest(row["input"]) == row["sha256"]


def test_search_schema_accepts_semantic_reason_and_is_backward_compatible():
    from jsonschema import Draft202012Validator

    schema = json.loads((Path(__file__).parents[1] / "schemas/search.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(
        {
            "clips": [],
            "next_cursor": None,
            "semantic": "ready",
            "scores": {"clip-000101": 1 / 61},
            "semantic_reason": "shard_corrupt",
        }
    )
    validator.validate({"clips": [], "next_cursor": None, "semantic": "pending"})
    validator.validate({"clips": [], "next_cursor": None})
    with pytest.raises(Exception):
        validator.validate({"clips": [], "next_cursor": None, "semantic": "done"})
