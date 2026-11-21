"""M22 r2 regression: batch 5000, M21 protected dual write and idempotency."""

import json
import warnings

import httpx
import pytest

from clipoteca.media.embedding_store import EmbeddingError, EmbeddingStore
from clipoteca.media.shards import GitHubShards, ShardStore
from test_shards_m22 import GitDataSimulator, vector


def test_dual_write_shard_failure_never_breaks_existing_m21(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise OSError("simulated M22 sidecar failure")

    monkeypatch.setattr(ShardStore, "put", fail)
    store = EmbeddingStore(tmp_path)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        metadata = store.put("clip-000101", vector(0))
    assert metadata["path"] == "clip-000101.f16"
    assert store.read("clip-000101")[0] == pytest.approx(1)
    assert any("m22_shard_dual_write_failed" in str(w.message) for w in captured)


def test_duplicates_within_one_batch_are_idempotent_but_not_mutable(tmp_path):
    store = ShardStore(tmp_path)
    first = store.put_many(
        [
            ("clip-000101", vector(0)),
            ("clip-000101", vector(0)),
        ]
    )
    assert first["tail"]["count"] == 1
    with pytest.raises(EmbeddingError, match="immutable"):
        store.put_many(
            [
                ("clip-000102", vector(0)),
                ("clip-000102", vector(1)),
            ]
        )
    assert store.manifest() == first


def test_remote_migration_batch_5000_one_manifest_cas_and_linear_head():
    remote = GitDataSimulator()
    remote.conflict_once = False
    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        publisher = GitHubShards(client, "riquelmechile/clipoteca-comunitaria")
        rows = [(f"clip-{i:06d}", vector(i % 2)) for i in range(1, 5001)]
        publisher.publish_many(rows)
        assert remote.count == 1
        publisher.publish_many(rows)
        assert remote.count == 1  # idempotent, no extra commit
    tree = remote.trees[remote.commits[remote.head]["tree"]]
    manifest = json.loads(tree["data/embeddings/manifest.json"])
    assert len(manifest["shards"]) == 19
    assert manifest["tail"]["count"] == 136
    assert len(tree) == 19 * 2 + 2 + 1
    assert sum(len(content) for content in tree.values()) < 5_700_000
