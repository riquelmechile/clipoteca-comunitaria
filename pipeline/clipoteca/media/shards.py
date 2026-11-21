"""M22 immutable content-addressed float16 shards with manifest CAS.

No 1-MiB GitHub Contents API read for vectors: existing immutable shards
are addressed through Git Data API blobs (git blob SHA-1). Small manifests
remain in Contents API; readers accept both M22 and the M21 sidecar index.
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
from typing import Callable

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
MAX_SHARD_BYTES = SHARD_CAPACITY * VECTOR_BYTES
SHARD_RE = re.compile(r"shards/shard-[0-9]{6}-[0-9a-f]{64}\.f16\Z")
QUERY_RE = re.compile(r"queries/[0-9a-f]{64}\.json\Z")


def normalized_query(text: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    if not normalized or len(normalized) > 200:
        raise ValueError("query must be 1..200 normalized characters")
    return normalized


def query_digest(text: str) -> str:
    return hashlib.sha256(normalized_query(text).encode("utf-8")).hexdigest()


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fresh_manifest() -> dict:
    return {
        "version": 2,
        "dimension": 512,
        "dtype": "float16-le",
        "shard_capacity": SHARD_CAPACITY,
        "shards": [],
    }


def validate_manifest(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("version") != 2
        or value.get("dimension") != DIMENSION
        or value.get("dtype") != "float16-le"
        or value.get("shard_capacity") != SHARD_CAPACITY
        or not isinstance(value.get("shards"), list)
        or len(value["shards"]) > 500
    ):
        raise EmbeddingError("embedding_manifest_invalid")
    seen: set[str] = set()
    for idx, shard in enumerate(value["shards"]):
        if not isinstance(shard, dict):
            raise EmbeddingError("embedding_shard_invalid")
        ids = shard.get("ids")
        count = shard.get("count")
        path = shard.get("path")
        digest = shard.get("sha256")
        blob_sha = shard.get("blob_sha")
        if (
            not isinstance(ids, list)
            or not isinstance(count, int)
            or not (1 <= count <= SHARD_CAPACITY)
            or len(ids) != count
            or not isinstance(path, str)
            or not SHARD_RE.fullmatch(path)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(blob_sha, str)
            or not re.fullmatch(r"[0-9a-f]{40}", blob_sha)
            or not path.startswith(f"shards/shard-{idx:06d}-")
            or path.rsplit("-", 1)[-1] != digest + ".f16"
        ):
            raise EmbeddingError("embedding_shard_invalid")
        for identifier in ids:
            if not isinstance(identifier, str) or not CLIP_ID_RE.fullmatch(identifier):
                raise EmbeddingError("embedding_shard_invalid_clip_id")
            if identifier in seen:
                raise EmbeddingError("embedding_duplicate_clip_id")
            seen.add(identifier)
    return value


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".shard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _append(
    manifest: dict,
    identifier: str,
    payload: bytes,
    load_blob: Callable[[dict], bytes],
) -> tuple[dict, str, bytes]:
    validate_manifest(manifest)
    if not CLIP_ID_RE.fullmatch(identifier):
        raise ValueError("invalid clip id")
    for shard in manifest["shards"]:
        if identifier in shard["ids"]:
            blob = load_blob(shard)
            offset = shard["ids"].index(identifier) * VECTOR_BYTES
            if blob[offset : offset + VECTOR_BYTES] != payload:
                raise EmbeddingError("immutable_clip_embedding_collision")
            return manifest, "", b""
    previous = manifest["shards"][-1] if manifest["shards"] else None
    if previous and previous["count"] < SHARD_CAPACITY:
        before = load_blob(previous)
        shard_number = len(manifest["shards"]) - 1
        ids = [*previous["ids"], identifier]
    else:
        before = b""
        shard_number = len(manifest["shards"])
        ids = [identifier]
    blob = before + payload
    digest = hashlib.sha256(blob).hexdigest()
    entry = {
        "path": f"shards/shard-{shard_number:06d}-{digest}.f16",
        "sha256": digest,
        "blob_sha": git_blob_sha(blob),
        "count": len(ids),
        "ids": ids,
    }
    updated = {
        **manifest,
        "shards": [*manifest["shards"][:shard_number], entry],
    }
    validate_manifest(updated)
    return updated, entry["path"], blob


def _check_blob(entry: dict, payload: bytes) -> bytes:
    if (
        len(payload) != entry["count"] * VECTOR_BYTES
        or len(payload) > MAX_SHARD_BYTES
        or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        or git_blob_sha(payload) != entry["blob_sha"]
    ):
        raise EmbeddingError("embedding_shard_checksum_or_size")
    return payload


class ShardStore:
    """Local immutable shard assembler; legacy M21 files remain readable."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def manifest(self) -> dict:
        if not self.manifest_path.is_file():
            return fresh_manifest()
        return validate_manifest(json.loads(self.manifest_path.read_text()))

    def _read_blob(self, shard: dict) -> bytes:
        path = self.root / shard["path"]
        return _check_blob(shard, path.read_bytes())

    def put(self, identifier: str, vector: np.ndarray) -> None:
        manifest, path, payload = _append(
            self.manifest(), identifier, vector_bytes(vector), self._read_blob
        )
        if path:
            _atomic(self.root / path, payload)
            _atomic(
                self.manifest_path,
                (
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                ).encode(),
            )

    def read(self, identifier: str) -> np.ndarray:
        for shard in self.manifest()["shards"]:
            if identifier in shard["ids"]:
                data = self._read_blob(shard)
                index = shard["ids"].index(identifier)
                return decode_vector(data[index * VECTOR_BYTES : (index + 1) * VECTOR_BYTES])
        # Transitional support for M21 index.json and per-clip .f16 files.
        from .embedding_store import EmbeddingStore

        return EmbeddingStore(self.root).read(identifier)


def migrate_legacy(root: Path) -> dict:
    """Convert every existing M21 per-clip float16 into deterministic shards.

    Leaves M21 sidecars in place until consumers have migrated; repeated calls
    are idempotent. No unpublished file is silently added.
    """
    from .embedding_store import EmbeddingStore

    legacy = EmbeddingStore(root)
    sharded = ShardStore(root)
    for identifier in sorted(legacy.index()["vectors"]):
        sharded.put(identifier, legacy.read(identifier))
    return sharded.manifest()


class GitHubShards:
    """Remote immutable shards + CAS manifest, with Git blob reads >1 MiB."""

    def __init__(self, client: httpx.Client, repository: str, branch: str = "main"):
        self.client = client
        self.repository = repository
        self.branch = branch
        self.root = f"https://api.github.com/repos/{repository}"

    def _contents(self, name: str) -> str:
        if name != "manifest.json" and not SHARD_RE.fullmatch(name):
            raise ValueError("invalid shard filename")
        return f"{self.root}/contents/data/embeddings/{name}"

    def _read_manifest(self) -> tuple[dict, str | None]:
        response = self.client.get(self._contents("manifest.json"), params={"ref": self.branch})
        if response.status_code == 404:
            return fresh_manifest(), None
        response.raise_for_status()
        obj = response.json()
        if obj.get("encoding") != "base64" or not isinstance(obj.get("sha"), str):
            raise EmbeddingError("remote_manifest_invalid")
        raw = base64.b64decode(obj["content"], validate=False)
        if len(raw) > 1_000_000:
            raise EmbeddingError("remote_manifest_size_limit")
        return validate_manifest(json.loads(raw)), obj["sha"]

    def _read_blob(self, entry: dict) -> bytes:
        # Git Data API, not Contents API's ~1MB decoded-body restriction.
        r = self.client.get(f"{self.root}/git/blobs/{entry['blob_sha']}")
        r.raise_for_status()
        obj = r.json()
        if obj.get("encoding") != "base64" or not isinstance(obj.get("content"), str):
            raise EmbeddingError("remote_git_blob_invalid")
        return _check_blob(entry, base64.b64decode(obj["content"]))

    def _put(self, path: str, payload: bytes, sha: str | None) -> bool:
        body = {
            "message": "chore(embeddings): update " + path,
            "content": base64.b64encode(payload).decode(),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        response = self.client.put(self._contents(path), json=body)
        if response.status_code in (409, 422):
            return False
        response.raise_for_status()
        return True

    def publish(self, identifier: str, vector: np.ndarray) -> None:
        raw = vector_bytes(vector)
        for _ in range(5):
            manifest, sha = self._read_manifest()
            updated, path, payload = _append(manifest, identifier, raw, self._read_blob)
            if not path:
                return
            # Content addressed, immutable. A stale upload may be orphaned
            # after a CAS conflict but never poisons the canonical manifest.
            existing = self.client.get(self._contents(path), params={"ref": self.branch})
            if existing.status_code == 404:
                if not self._put(path, payload, None):
                    continue
            elif existing.status_code == 200:
                remote_sha = existing.json().get("sha")
                if remote_sha != git_blob_sha(payload):
                    raise EmbeddingError("remote_immutable_shard_collision")
            else:
                existing.raise_for_status()
            body = (
                json.dumps(updated, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
            ).encode()
            if self._put("manifest.json", body, sha):
                return
        raise EmbeddingError("remote_manifest_conflict")


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
