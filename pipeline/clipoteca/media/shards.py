"""M22 r2 bounded immutable sealed shards, mutable tail and batched Git CAS.

Sealed shards are immutable content addressed blobs. The *only* mutable vector
file is shards/tail.f16 (<=256 vectors), updated in the same Git tree commit as
the manifest and its ids file. IDs live outside the small manifest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Callable, Sequence

import httpx
import numpy as np

from .embedding_store import (
    CLIP_ID_RE,
    DIMENSION,
    EmbeddingError,
    VECTOR_BYTES,
    decode_vector,
    vector_bytes,
)

SHARD_CAPACITY = 4096
SEAL_CAPACITY = 256
MAX_SHARDS = 500
MAX_SHARD_BYTES = SHARD_CAPACITY * VECTOR_BYTES
TAIL_PATH = "shards/tail.f16"
TAIL_IDS_PATH = "ids/tail.json"
SHARD_RE = re.compile(r"shards/shard-[0-9]{6}-[0-9a-f]{64}\.f16\Z")
IDS_RE = re.compile(r"ids/shard-[0-9]{6}-[0-9a-f]{64}\.json\Z")


def normalized_query(text: str) -> str:
    # Unicode casefold also handles Greek final sigma, Cherokee and sharp S.
    # U+FEFF is made a space deliberately to match the Worker regex.
    canonical = unicodedata.normalize("NFKC", text).casefold().replace("\ufeff", " ")
    normalized = " ".join(canonical.split())
    if not normalized or len(normalized) > 200:
        raise ValueError("query must be 1..200 normalized characters")
    return normalized


def query_digest(text: str) -> str:
    return hashlib.sha256(normalized_query(text).encode("utf-8")).hexdigest()


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fresh_manifest() -> dict:
    return {
        "version": 3,
        "dimension": DIMENSION,
        "dtype": "float16-le",
        "shard_capacity": SHARD_CAPACITY,
        "seal_capacity": SEAL_CAPACITY,
        "shards": [],
        "tail": None,
    }


def _ids_bytes(ids: list[str]) -> bytes:
    return (json.dumps(ids, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _entry(path: str, ids_path: str, payload: bytes, ids: list[str]) -> dict:
    ids_payload = _ids_bytes(ids)
    return {
        "path": path,
        "ids_path": ids_path,
        "count": len(ids),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "blob_sha": git_blob_sha(payload),
        "ids_sha256": hashlib.sha256(ids_payload).hexdigest(),
        "ids_blob_sha": git_blob_sha(ids_payload),
    }


def validate_manifest(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("version") != 3
        or value.get("dimension") != DIMENSION
        or value.get("dtype") != "float16-le"
        or value.get("shard_capacity") != SHARD_CAPACITY
        or value.get("seal_capacity") != SEAL_CAPACITY
        or not isinstance(value.get("shards"), list)
        or len(value["shards"]) > MAX_SHARDS
        or value.get("tail") is not None
        and not isinstance(value["tail"], dict)
    ):
        raise EmbeddingError("embedding_manifest_invalid")
    for index, entry in enumerate([*value["shards"], *([value["tail"]] if value["tail"] else [])]):
        sealed = index < len(value["shards"])
        if not isinstance(entry, dict):
            raise EmbeddingError("embedding_shard_invalid")
        count = entry.get("count")
        if not isinstance(count, int) or not 1 <= count <= SEAL_CAPACITY:
            raise EmbeddingError("embedding_shard_invalid")
        if sealed and count != SEAL_CAPACITY:
            raise EmbeddingError("embedding_sealed_shard_not_full")
        for key, size in (
            ("sha256", 64),
            ("ids_sha256", 64),
            ("blob_sha", 40),
            ("ids_blob_sha", 40),
        ):
            if not isinstance(entry.get(key), str) or not re.fullmatch(
                "[0-9a-f]{" + str(size) + "}", entry[key]
            ):
                raise EmbeddingError("embedding_shard_invalid")
        if sealed:
            if not isinstance(entry.get("path"), str) or not SHARD_RE.fullmatch(entry["path"]):
                raise EmbeddingError("embedding_shard_invalid")
            if not isinstance(entry.get("ids_path"), str) or not IDS_RE.fullmatch(
                entry["ids_path"]
            ):
                raise EmbeddingError("embedding_shard_invalid")
            prefix = f"{index:06d}-"
            if not entry["path"].startswith("shards/shard-" + prefix) or not entry["path"].endswith(
                entry["sha256"] + ".f16"
            ):
                raise EmbeddingError("embedding_shard_invalid")
            if not entry["ids_path"].startswith("ids/shard-" + prefix) or not entry[
                "ids_path"
            ].endswith(entry["ids_sha256"] + ".json"):
                raise EmbeddingError("embedding_shard_invalid")
        elif entry.get("path") != TAIL_PATH or entry.get("ids_path") != TAIL_IDS_PATH:
            raise EmbeddingError("embedding_tail_invalid")
    return value


def _check_blob(entry: dict, payload: bytes, *, ids: bool = False) -> bytes:
    expected_size = MAX_SHARD_BYTES if not ids else SEAL_CAPACITY * 18
    if len(payload) > expected_size or hashlib.sha256(payload).hexdigest() != (
        entry["ids_sha256"] if ids else entry["sha256"]
    ):
        raise EmbeddingError("embedding_shard_checksum_or_size")
    if git_blob_sha(payload) != (entry["ids_blob_sha"] if ids else entry["blob_sha"]):
        raise EmbeddingError("embedding_shard_checksum_or_size")
    if not ids and len(payload) != entry["count"] * VECTOR_BYTES:
        raise EmbeddingError("embedding_shard_checksum_or_size")
    return payload


def _valid_ids(entry: dict, raw: bytes) -> list[str]:
    ids = json.loads(_check_blob(entry, raw, ids=True))
    if (
        not isinstance(ids, list)
        or len(ids) != entry["count"]
        or any(not isinstance(i, str) or not CLIP_ID_RE.fullmatch(i) for i in ids)
        or len(ids) != len(set(ids))
    ):
        raise EmbeddingError("embedding_shard_ids_invalid")
    return ids


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".shard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _manifest_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _append_many(
    source: dict,
    rows: Sequence[tuple[str, bytes]],
    read: Callable[[dict, bool], bytes],
) -> tuple[dict, dict[str, bytes | None]]:
    """Return exactly one new manifest and minimal atomic path changes.

    Each 256-vector sealed shard is written *once*.  Subsequent inserts mutate
    only a 256 KiB bounded tail at stable paths. An existing ID is immutable.
    """
    original = validate_manifest(source)
    manifest = {**original, "shards": [dict(shard) for shard in original["shards"]]}
    changes: dict[str, bytes | None] = {}
    existing: dict[str, tuple[dict, int]] = {}
    for entry in manifest["shards"]:
        for position, identifier in enumerate(_valid_ids(entry, read(entry, True))):
            if identifier in existing:
                raise EmbeddingError("embedding_duplicate_clip_id")
            existing[identifier] = (entry, position)
    tail = manifest["tail"]
    tail_ids: list[str] = []
    tail_blob = b""
    if tail is not None:
        tail_ids = _valid_ids(tail, read(tail, True))
        tail_blob = _check_blob(tail, read(tail, False))
        for position, identifier in enumerate(tail_ids):
            if identifier in existing:
                raise EmbeddingError("embedding_duplicate_clip_id")
            existing[identifier] = (tail, position)
    newly_added: dict[str, bytes] = {}
    for identifier, data in rows:
        if not CLIP_ID_RE.fullmatch(identifier) or len(data) != VECTOR_BYTES:
            raise ValueError("invalid clip vector")
        prior_new = newly_added.get(identifier)
        if prior_new is not None:
            if prior_new != data:
                raise EmbeddingError("immutable_clip_embedding_collision")
            continue
        prior = existing.get(identifier)
        if prior is not None:
            old, offset = prior
            if old is tail:
                current = tail_blob[offset * VECTOR_BYTES : (offset + 1) * VECTOR_BYTES]
            else:
                current = read(old, False)[offset * VECTOR_BYTES : (offset + 1) * VECTOR_BYTES]
            if current != data:
                raise EmbeddingError("immutable_clip_embedding_collision")
            continue
        newly_added[identifier] = data
        tail_ids.append(identifier)
        tail_blob += data
        if len(tail_ids) == SEAL_CAPACITY:
            index = len(manifest["shards"])
            digest = hashlib.sha256(tail_blob).hexdigest()
            ids_raw = _ids_bytes(tail_ids)
            ids_digest = hashlib.sha256(ids_raw).hexdigest()
            path = f"shards/shard-{index:06d}-{digest}.f16"
            ids_path = f"ids/shard-{index:06d}-{ids_digest}.json"
            manifest["shards"].append(_entry(path, ids_path, tail_blob, tail_ids))
            changes[path] = tail_blob
            changes[ids_path] = ids_raw
            changes[TAIL_PATH] = None
            changes[TAIL_IDS_PATH] = None
            tail_ids, tail_blob = [], b""
    if not newly_added:
        # A repeated batch must not write unchanged tail or advance the branch.
        return original, {}
    if tail_ids:
        manifest["tail"] = _entry(TAIL_PATH, TAIL_IDS_PATH, tail_blob, tail_ids)
        changes[TAIL_PATH] = tail_blob
        changes[TAIL_IDS_PATH] = _ids_bytes(tail_ids)
    else:
        manifest["tail"] = None
    if len(manifest["shards"]) > MAX_SHARDS:
        raise EmbeddingError("embedding_shard_limit")
    if rows and changes:
        validate_manifest(manifest)
        changes["manifest.json"] = _manifest_bytes(manifest)
    return manifest, changes


class ShardStore:
    """Local durable M22 shards and transitional M21 per-clip reader."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def manifest(self) -> dict:
        if not self.manifest_path.is_file():
            return fresh_manifest()
        return validate_manifest(json.loads(self.manifest_path.read_text()))

    def _read(self, entry: dict, ids: bool) -> bytes:
        path = self.root / (entry["ids_path"] if ids else entry["path"])
        data = path.read_bytes()
        return _check_blob(entry, data, ids=ids)

    def put_many(self, vectors: Sequence[tuple[str, np.ndarray]]) -> dict:
        rows = [(identifier, vector_bytes(vector)) for identifier, vector in vectors]
        manifest, changes = _append_many(self.manifest(), rows, self._read)
        if not changes:
            return manifest
        # Files are written before the new manifest; old sealed paths persist.
        for path, data in changes.items():
            if data is not None and path != "manifest.json":
                _atomic(self.root / path, data)
        _atomic(self.manifest_path, changes["manifest.json"])
        for path, data in changes.items():
            if data is None:
                (self.root / path).unlink(missing_ok=True)
        return manifest

    def put(self, identifier: str, vector: np.ndarray) -> None:
        self.put_many([(identifier, vector)])

    def read(self, identifier: str) -> np.ndarray:
        for entry in [
            *self.manifest()["shards"],
            *([self.manifest()["tail"]] if self.manifest()["tail"] else []),
        ]:
            ids = _valid_ids(entry, self._read(entry, True))
            if identifier in ids:
                index = ids.index(identifier)
                blob = self._read(entry, False)
                return decode_vector(blob[index * VECTOR_BYTES : (index + 1) * VECTOR_BYTES])
        from .embedding_store import EmbeddingStore

        return EmbeddingStore(self.root).read(identifier)


def migrate_legacy(root: Path) -> dict:
    """Read existing M21 index and convert all vectors in ONE batch."""
    from .embedding_store import EmbeddingStore

    legacy = EmbeddingStore(root)
    rows = [
        (identifier, legacy.read(identifier)) for identifier in sorted(legacy.index()["vectors"])
    ]
    return ShardStore(root).put_many(rows)


class GitHubShards:
    """Single Git Data API transaction per shard batch, optimistic branch CAS."""

    def __init__(self, client: httpx.Client, repository: str, branch: str = "main"):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository) or branch != "main":
            raise ValueError("invalid shard publication repository")
        self.client, self.repository, self.branch = client, repository, branch
        self.root = f"https://api.github.com/repos/{repository}"

    def _get_manifest(self, head: str) -> dict:
        result = self.client.get(
            f"{self.root}/contents/data/embeddings/manifest.json", params={"ref": head}
        )
        if result.status_code == 404:
            return fresh_manifest()
        result.raise_for_status()
        obj = result.json()
        if obj.get("encoding") != "base64" or not isinstance(obj.get("content"), str):
            raise EmbeddingError("remote_manifest_invalid")
        raw = base64.b64decode(obj["content"])
        if len(raw) > 1_000_000:
            raise EmbeddingError("manifest_too_large")
        return validate_manifest(json.loads(raw))

    def _blob(self, entry: dict, ids: bool) -> bytes:
        key = "ids_blob_sha" if ids else "blob_sha"
        result = self.client.get(f"{self.root}/git/blobs/{entry[key]}")
        result.raise_for_status()
        obj = result.json()
        if obj.get("encoding") != "base64" or not isinstance(obj.get("content"), str):
            raise EmbeddingError("remote_blob_invalid")
        return _check_blob(entry, base64.b64decode(obj["content"]), ids=ids)

    def publish_many(self, vectors: Sequence[tuple[str, np.ndarray]]) -> None:
        rows = [(identifier, vector_bytes(vector)) for identifier, vector in vectors]
        if not rows:
            return
        for _ in range(5):
            ref = self.client.get(f"{self.root}/git/ref/heads/{self.branch}")
            ref.raise_for_status()
            head = ref.json()["object"]["sha"]
            manifest = self._get_manifest(head)
            _updated, changes = _append_many(manifest, rows, self._blob)
            if not changes:
                return
            parent = self.client.get(f"{self.root}/git/commits/{head}")
            parent.raise_for_status()
            tree = parent.json()["tree"]["sha"]
            entries = []
            for path, content in changes.items():
                sha: str | None = None
                if content is not None:
                    blob = self.client.post(
                        f"{self.root}/git/blobs",
                        json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
                    )
                    blob.raise_for_status()
                    sha = blob.json()["sha"]
                    if sha != git_blob_sha(content):
                        raise EmbeddingError("remote_blob_sha_mismatch")
                entries.append(
                    {
                        "path": "data/embeddings/" + path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": sha,
                    }
                )
            update_tree = self.client.post(
                f"{self.root}/git/trees", json={"base_tree": tree, "tree": entries}
            )
            update_tree.raise_for_status()
            commit = self.client.post(
                f"{self.root}/git/commits",
                json={
                    "message": "chore(embeddings): publish shard batch",
                    "tree": update_tree.json()["sha"],
                    "parents": [head],
                },
            )
            commit.raise_for_status()
            commit_sha = commit.json()["sha"]
            move = self.client.patch(
                f"{self.root}/git/refs/heads/{self.branch}",
                json={"sha": commit_sha, "force": False},
            )
            if move.status_code in (409, 422):
                continue
            move.raise_for_status()
            return
        raise EmbeddingError("remote_manifest_conflict")

    def publish(self, identifier: str, vector: np.ndarray) -> None:
        self.publish_many([(identifier, vector)])


class QueryStore:
    """Normalized SHA-256 address, no raw request term in a filename."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, query: str, vector: np.ndarray) -> Path:
        digest = query_digest(query)
        path = self.root / "queries" / f"{digest}.json"
        payload = {
            "version": 1,
            "query_sha256": digest,
            "dimension": DIMENSION,
            "dtype": "float16-le",
            "vector": base64.b64encode(vector_bytes(vector)).decode(),
        }
        contents = (json.dumps(payload, sort_keys=True) + "\n").encode()
        if path.exists() and path.read_bytes() != contents:
            raise EmbeddingError("immutable_query_vector_collision")
        if not path.exists():
            _atomic(path, contents)
        return path

    def read(self, query: str) -> np.ndarray:
        payload = json.loads((self.root / "queries" / f"{query_digest(query)}.json").read_text())
        if payload.get("query_sha256") != query_digest(query) or payload.get("dimension") != 512:
            raise EmbeddingError("query_vector_invalid")
        return decode_vector(base64.b64decode(payload["vector"], validate=True))


class GitHubQueries:
    def __init__(self, client: httpx.Client, repository: str, branch: str = "main"):
        self.client, self.repository, self.branch = client, repository, branch

    def publish(self, query: str, vector: np.ndarray) -> None:
        digest = query_digest(query)
        body = {
            "version": 1,
            "query_sha256": digest,
            "dimension": 512,
            "dtype": "float16-le",
            "vector": base64.b64encode(vector_bytes(vector)).decode(),
        }
        raw = (json.dumps(body, sort_keys=True) + "\n").encode()
        url = (
            f"https://api.github.com/repos/{self.repository}"
            f"/contents/data/embeddings/queries/{digest}.json"
        )
        old = self.client.get(url, params={"ref": self.branch})
        if old.status_code == 200:
            doc = old.json()
            if base64.b64decode(doc.get("content", "")) == raw:
                return
            raise EmbeddingError("immutable_query_vector_collision")
        if old.status_code != 404:
            old.raise_for_status()
        result = self.client.put(
            url,
            json={
                "message": "chore(embeddings): cache semantic query",
                "content": base64.b64encode(raw).decode(),
                "branch": self.branch,
            },
        )
        if result.status_code in (409, 422):
            current = self.client.get(url, params={"ref": self.branch})
            if (
                current.status_code != 200
                or base64.b64decode(current.json().get("content", "")) != raw
            ):
                raise EmbeddingError("query_vector_conflict")
            return
        result.raise_for_status()
