"""M22 r3 bounded query cache: 512 entries, atomic CAS and FIFO shard pruning."""

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from clipoteca.media.embedding_store import EmbeddingError
from clipoteca.media.query_shards import (
    CAPACITY,
    MAX_QUERIES,
    GitHubQueries,
    QueryStore,
)
from test_shards_m22 import GitDataSimulator


def vector() -> np.ndarray:
    data = np.zeros(512, dtype=np.float32)
    data[0] = 1
    return data


def test_local_query_shards_prune_oldest_and_keep_bounded_tree(tmp_path):
    store = QueryStore(tmp_path)
    rows = [(f"query-{i}", vector()) for i in range(600)]
    store.put_many(rows)
    manifest = store.manifest()
    assert CAPACITY == 64 and MAX_QUERIES == 512
    assert len(manifest["shards"]) == 7
    assert manifest["tail"]["count"] == 24
    files = [x for x in (tmp_path / "queries").rglob("*") if x.is_file()]
    assert len(files) == 7 * 2 + 2 + 1
    assert sum(x.stat().st_size for x in files) < 600_000
    assert store.read("query-599")[0] == pytest.approx(1)
    with pytest.raises(EmbeddingError, match="not_cached"):
        store.read("query-0")
    before = manifest
    store.put_many(rows[-20:])
    assert store.manifest() == before


def test_remote_query_cache_single_git_cas_409_then_idempotent(tmp_path):
    remote = GitDataSimulator()
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        publisher = GitHubQueries(client, "riquelmechile/clipoteca-comunitaria")
        publisher.publish_many([(f"query-{i}", vector()) for i in range(600)])
        old_count = remote.count
        assert old_count >= 2  # first CAS conflict, then success
        publisher.publish_many([(f"query-{i}", vector()) for i in range(590, 600)])
        assert remote.count == old_count  # idempotent
    tree = remote.trees[remote.commits[remote.head]["tree"]]
    manifest = json.loads(tree["data/embeddings/queries/manifest.json"])
    assert len(manifest["shards"]) == 7
    assert len(tree) == 7 * 2 + 2 + 1
    assert sum(len(x) for x in tree.values()) < 600_000
    assert not any(
        x.endswith(".json") and "/queries/" in x and len(Path(x).stem) == 64 for x in tree
    )


def test_query_shards_reject_mutating_existing_normalized_query(tmp_path):
    store = QueryStore(tmp_path)
    first = vector()
    store.put("  AGUACERO ", first)
    changed = np.zeros(512, dtype=np.float32)
    changed[1] = 1
    with pytest.raises(EmbeddingError, match="immutable"):
        store.put("aguacero", changed)


def test_exact_256_clip_batch_requires_no_missing_tail_deletions():
    from clipoteca.media.shards import GitHubShards

    remote = GitDataSimulator()
    remote.conflict_once = False
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        rows = [(f"clip-{i:06d}", vector()) for i in range(1, 257)]
        GitHubShards(client, "riquelmechile/clipoteca-comunitaria").publish_many(rows)
    tree = remote.trees[remote.commits[remote.head]["tree"]]
    assert len(tree) == 3  # manifest and two sealed blobs, no absent sha:null
    assert not any("/tail-" in name for name in tree)


def test_m21_sidecars_opt_out_after_migration(monkeypatch, tmp_path):
    from clipoteca.media.embedding_store import EmbeddingStore
    from clipoteca.media.shards import ShardStore

    monkeypatch.setenv("CLIPOTECA_M21_SIDECARS", "0")
    store = EmbeddingStore(tmp_path)
    store.put("clip-000101", vector())
    assert not (tmp_path / "index.json").exists()
    assert not (tmp_path / "clip-000101.f16").exists()
    assert ShardStore(tmp_path).read("clip-000101")[0] == pytest.approx(1)
    assert store.write_warnings == {}


def test_local_m21_failure_still_saves_m22_and_exposes_warning(monkeypatch, tmp_path):
    from clipoteca.media.embedding_store import EmbeddingStore
    from clipoteca.media.shards import ShardStore

    monkeypatch.setenv("CLIPOTECA_M21_SIDECARS", "1")

    def broken_index(_self):
        raise OSError("synthetic read failure")

    monkeypatch.setattr(EmbeddingStore, "index", broken_index)
    store = EmbeddingStore(tmp_path)
    with pytest.warns(RuntimeWarning, match="m21_sidecar_persist_failed"):
        store.put("clip-000101", vector())
    assert store.write_warnings["m21"] == "m21_sidecar_persist_failed:clip_published"
    assert ShardStore(tmp_path).read("clip-000101")[0] == pytest.approx(1)
