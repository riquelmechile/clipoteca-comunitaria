"""Bounded, CAS-published query embeddings: sealed 64-vector shards + hashed tail.

At most 512 cached queries in HEAD. Eviction is FIFO by sealed shard, a
bounded deterministic approximation to oldest/least-recently-used, with one
Git Data API commit per job and conditional fast-forward retries on conflict.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Sequence

import httpx
import numpy as np

from .embedding_store import EmbeddingError, VECTOR_BYTES, decode_vector, vector_bytes
from .shards import _atomic, git_blob_sha, query_digest

CAPACITY = 64
MAX_QUERIES = 512
QUERY_ID = re.compile(r"[0-9a-f]{64}\Z")


def _json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _entry(seq: int, ids: list[str], data: bytes, sealed: bool) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    ids_raw = _json(ids)
    ids_digest = hashlib.sha256(ids_raw).hexdigest()
    prefix = f"shard-{seq:06d}" if sealed else "tail"
    return {
        "seq": seq,
        "path": f"shards/{prefix}-{digest}.f16",
        "ids_path": f"ids/{prefix}-{ids_digest}.json",
        "count": len(ids),
        "sha256": digest,
        "ids_sha256": ids_digest,
        "blob_sha": git_blob_sha(data),
        "ids_blob_sha": git_blob_sha(ids_raw),
    }


def empty_manifest() -> dict:
    return {
        "version": 1,
        "dimension": 512,
        "dtype": "float16-le",
        "seal_capacity": CAPACITY,
        "max_queries": MAX_QUERIES,
        "next_seq": 0,
        "shards": [],
        "tail": None,
    }


def validate_query_manifest(value: object) -> dict:
    if (
        not isinstance(value, dict)
        or any(
            value.get(k) != v
            for k, v in (
                ("version", 1),
                ("dimension", 512),
                ("dtype", "float16-le"),
                ("seal_capacity", CAPACITY),
                ("max_queries", MAX_QUERIES),
            )
        )
        or not isinstance(value.get("next_seq"), int)
        or not isinstance(value.get("shards"), list)
        or len(value["shards"]) > MAX_QUERIES // CAPACITY
    ):
        raise EmbeddingError("query_manifest_invalid")
    previous_seq = -1
    for index, item in enumerate(
        [*value["shards"], *([value["tail"]] if value.get("tail") else [])]
    ):
        sealed = index < len(value["shards"])
        if not isinstance(item, dict) or not isinstance(item.get("seq"), int):
            raise EmbeddingError("query_manifest_invalid")
        if item["seq"] < previous_seq or sealed and item["seq"] == previous_seq:
            raise EmbeddingError("query_manifest_invalid")
        previous_seq = item["seq"]
        if (
            not isinstance(item.get("count"), int)
            or item["count"] < 1
            or item["count"] > CAPACITY
            or sealed
            and item["count"] != CAPACITY
        ):
            raise EmbeddingError("query_manifest_invalid")
        for k in ("sha256", "ids_sha256", "blob_sha", "ids_blob_sha"):
            expected = 40 if k.endswith("blob_sha") else 64
            if not isinstance(item.get(k), str) or not re.fullmatch(
                r"[0-9a-f]{" + str(expected) + "}", item[k]
            ):
                raise EmbeddingError("query_manifest_invalid")
        prefix = f"shard-{item['seq']:06d}" if sealed else "tail"
        if (
            item.get("path") != f"shards/{prefix}-{item['sha256']}.f16"
            or item.get("ids_path") != f"ids/{prefix}-{item['ids_sha256']}.json"
        ):
            raise EmbeddingError("query_manifest_invalid")
    if value["next_seq"] < previous_seq:
        raise EmbeddingError("query_manifest_invalid")
    if (
        sum(x["count"] for x in value["shards"])
        + (value["tail"]["count"] if value.get("tail") else 0)
        > MAX_QUERIES
    ):
        raise EmbeddingError("query_manifest_limit")
    return value


def _checked(item: dict, raw: bytes, is_ids: bool) -> bytes:
    sha_key = "ids_sha256" if is_ids else "sha256"
    blob_key = "ids_blob_sha" if is_ids else "blob_sha"
    if (
        len(raw) > (CAPACITY * 70 if is_ids else CAPACITY * VECTOR_BYTES)
        or hashlib.sha256(raw).hexdigest() != item[sha_key]
        or git_blob_sha(raw) != item[blob_key]
    ):
        raise EmbeddingError("query_shard_corrupt")
    if not is_ids and len(raw) != item["count"] * VECTOR_BYTES:
        raise EmbeddingError("query_shard_corrupt")
    return raw


def _ids(item: dict, read: Callable[[dict, bool], bytes]) -> list[str]:
    value = json.loads(_checked(item, read(item, True), True))
    if (
        not isinstance(value, list)
        or len(value) != item["count"]
        or any(not isinstance(i, str) or not QUERY_ID.fullmatch(i) for i in value)
        or len(set(value)) != len(value)
    ):
        raise EmbeddingError("query_shard_ids_corrupt")
    return value


def change_batch(
    source: dict,
    rows: Sequence[tuple[str, bytes]],
    read: Callable[[dict, bool], bytes],
) -> tuple[dict, dict[str, bytes | None]]:
    old = validate_query_manifest(source)
    current = {**old, "shards": list(old["shards"])}
    paths: dict[str, bytes | None] = {}
    known: dict[str, tuple[dict, int]] = {}
    for item in current["shards"]:
        for index, id_ in enumerate(_ids(item, read)):
            known[id_] = (item, index)
    tail = current["tail"]
    tail_ids = _ids(tail, read) if tail else []
    tail_data = _checked(tail, read(tail, False), False) if tail else b""
    if tail:
        for index, id_ in enumerate(tail_ids):
            known[id_] = (tail, index)
    additions = {}
    for identifier, raw in rows:
        if not QUERY_ID.fullmatch(identifier) or len(raw) != VECTOR_BYTES:
            raise ValueError("invalid query embedding")
        if identifier in additions:
            if additions[identifier] != raw:
                raise EmbeddingError("immutable_query_vector_collision")
            continue
        existing = known.get(identifier)
        if existing:
            item, index = existing
            original = tail_data if item is tail else _checked(item, read(item, False), False)
            if original[index * VECTOR_BYTES : (index + 1) * VECTOR_BYTES] != raw:
                raise EmbeddingError("immutable_query_vector_collision")
            continue
        additions[identifier] = raw
        tail_ids.append(identifier)
        tail_data += raw
        if len(tail_ids) == CAPACITY:
            index = current["next_seq"]
            entry = _entry(index, tail_ids, tail_data, True)
            current["shards"].append(entry)
            current["next_seq"] += 1
            paths[entry["path"]] = tail_data
            paths[entry["ids_path"]] = _json(tail_ids)
            tail_ids, tail_data = [], b""
    if not additions:
        return old, {}
    if tail:
        paths[tail["path"]] = None
        paths[tail["ids_path"]] = None
    if tail_ids:
        current["tail"] = _entry(current["next_seq"], tail_ids, tail_data, False)
        entry = current["tail"]
        paths[entry["path"]] = tail_data
        paths[entry["ids_path"]] = _json(tail_ids)
    else:
        current["tail"] = None
    # Oldest sealed shards are pruned as whole 64-query blocks. The tail
    # and newest sealed shards are preserved; max 512 total cached vectors.
    while (
        sum(x["count"] for x in current["shards"])
        + (current["tail"]["count"] if current["tail"] else 0)
        > MAX_QUERIES
    ):
        oldest = current["shards"].pop(0)
        paths[oldest["path"]] = None
        paths[oldest["ids_path"]] = None
    validate_query_manifest(current)
    paths["manifest.json"] = _json(current)
    return current, paths


class QueryStore:
    def __init__(self, root: Path):
        self.root = Path(root) / "queries"

    def _read(self, item: dict, ids: bool) -> bytes:
        return (self.root / (item["ids_path"] if ids else item["path"])).read_bytes()

    def manifest(self) -> dict:
        path = self.root / "manifest.json"
        if path.is_file():
            return validate_query_manifest(json.loads(path.read_text()))
        return empty_manifest()

    def put_many(self, queries: Sequence[tuple[str, np.ndarray]]) -> Path:
        items = [(query_digest(query), vector_bytes(vector)) for query, vector in queries]
        updated, paths = change_batch(self.manifest(), items, self._read)
        if paths:
            for name, data in paths.items():
                if data is not None and name != "manifest.json":
                    _atomic(self.root / name, data)
            _atomic(self.root / "manifest.json", paths["manifest.json"])
            for name, data in paths.items():
                if data is None:
                    (self.root / name).unlink(missing_ok=True)
        return self.root / "manifest.json"

    def put(self, query: str, vector: np.ndarray) -> Path:
        return self.put_many([(query, vector)])

    def read(self, query: str) -> np.ndarray:
        digest = query_digest(query)
        manifest = self.manifest()
        for item in [*manifest["shards"], *([manifest["tail"]] if manifest["tail"] else [])]:
            ids = _ids(item, self._read)
            if digest in ids:
                index = ids.index(digest)
                data = _checked(item, self._read(item, False), False)
                return decode_vector(data[index * VECTOR_BYTES : (index + 1) * VECTOR_BYTES])
        raise EmbeddingError("query_vector_not_cached")


class GitHubQueries:
    """One commit per publishing job, retry optimistic HEAD conflicts."""

    def __init__(self, client: httpx.Client, repository: str, branch: str = "main"):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or branch != "main":
            raise ValueError("invalid query publication")
        self.client, self.repository, self.branch = client, repository, branch
        self.root = f"https://api.github.com/repos/{repository}"

    def _request(self, method: str, suffix: str, **kwargs):
        response = self.client.request(method, f"{self.root}/{suffix}", **kwargs)
        response.raise_for_status()
        return response.json()

    def _manifest(self, head: str) -> dict:
        response = self.client.get(
            f"{self.root}/contents/data/embeddings/queries/manifest.json",
            params={"ref": head},
        )
        if response.status_code == 404:
            return empty_manifest()
        response.raise_for_status()
        result = response.json()
        raw = base64.b64decode(result["content"])
        if len(raw) > 100000:
            raise EmbeddingError("query_manifest_too_large")
        return validate_query_manifest(json.loads(raw))

    def _read(self, item: dict, ids: bool) -> bytes:
        sha = item["ids_blob_sha"] if ids else item["blob_sha"]
        doc = self._request("GET", f"git/blobs/{sha}")
        return _checked(item, base64.b64decode(doc["content"]), ids)

    def publish_many(self, queries: Sequence[tuple[str, np.ndarray]]) -> None:
        rows = [(query_digest(query), vector_bytes(vector)) for query, vector in queries]
        if not rows:
            return
        for _ in range(5):
            ref = self._request("GET", f"git/ref/heads/{self.branch}")
            head = ref["object"]["sha"]
            manifest = self._manifest(head)
            _updated, changes = change_batch(manifest, rows, self._read)
            if not changes:
                return
            parent = self._request("GET", f"git/commits/{head}")
            entries = []
            for name, content in changes.items():
                sha = None
                if content is not None:
                    doc = self._request(
                        "POST",
                        "git/blobs",
                        json={
                            "content": base64.b64encode(content).decode(),
                            "encoding": "base64",
                        },
                    )
                    sha = doc["sha"]
                    if sha != git_blob_sha(content):
                        raise EmbeddingError("query_blob_sha_mismatch")
                entries.append(
                    {
                        "path": "data/embeddings/queries/" + name,
                        "mode": "100644",
                        "type": "blob",
                        "sha": sha,
                    }
                )
            tree = self._request(
                "POST",
                "git/trees",
                json={
                    "base_tree": parent["tree"]["sha"],
                    "tree": entries,
                },
            )
            commit = self._request(
                "POST",
                "git/commits",
                json={
                    "message": "chore(embeddings): cache query shard batch",
                    "tree": tree["sha"],
                    "parents": [head],
                },
            )
            moved = self.client.patch(
                f"{self.root}/git/refs/heads/{self.branch}",
                json={"sha": commit["sha"], "force": False},
            )
            if moved.status_code in (409, 422):
                continue
            moved.raise_for_status()
            return
        raise EmbeddingError("query_shards_cas_conflict")

    def publish(self, query: str, vector: np.ndarray) -> None:
        self.publish_many([(query, vector)])
