"""Durable M21 clip vector files: little-endian float16 plus public ID index.

Local writes are atomic; optional GitHub upload uses optimistic Contents API
and never overwrites an existing clip ID with different vector bytes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import httpx
import numpy as np

from .embeddings import DIMENSION, EmbeddingError, normalize_vector

CLIP_ID_RE = re.compile(r"clip-[0-9]{6}\Z")
REPOSITORY_RE = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\Z")
VECTOR_BYTES = DIMENSION * 2
INDEX_VERSION = 1


def new_index() -> dict:
    return {"version": INDEX_VERSION, "dimension": DIMENSION, "dtype": "float16-le", "vectors": {}}


def vector_bytes(vector: np.ndarray) -> bytes:
    """Normalize in float32, quantize once, store 1024 little-endian bytes."""
    quantized = normalize_vector(vector).astype("<f2")
    return quantized.tobytes(order="C")


def decode_vector(payload: bytes) -> np.ndarray:
    if len(payload) != VECTOR_BYTES:
        raise EmbeddingError("embedding_binary_size_invalid")
    vector = np.frombuffer(payload, dtype="<f2").astype(np.float32)
    if not np.all(np.isfinite(vector)):
        raise EmbeddingError("embedding_binary_non_finite")
    norm = float(np.linalg.norm(vector))
    if not 0.98 <= norm <= 1.02:
        raise EmbeddingError("embedding_binary_not_normalized")
    return vector


def _metadata(identifier: str, payload: bytes) -> dict:
    if not CLIP_ID_RE.fullmatch(identifier):
        raise ValueError("invalid clip identifier")
    decode_vector(payload)
    return {
        "path": identifier + ".f16",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _check_index(index: dict) -> None:
    if (
        not isinstance(index, dict)
        or any(
            index.get(k) != v
            for k, v in (
                ("version", INDEX_VERSION),
                ("dimension", DIMENSION),
                ("dtype", "float16-le"),
            )
        )
        or not isinstance(index.get("vectors"), dict)
    ):
        raise EmbeddingError("embedding_index_invalid")


def _atomic_write(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".embedding-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EmbeddingStore:
    """One file per clip + index keyed by immutable public catalogue ID."""

    def __init__(self, root: Path = Path("data/embeddings")):
        self.root = Path(root)
        self.index_file = self.root / "index.json"

    def index(self) -> dict:
        if not self.index_file.exists():
            return new_index()
        result = json.loads(self.index_file.read_text(encoding="utf-8"))
        _check_index(result)
        return result

    def put(self, identifier: str, vector: np.ndarray) -> dict:
        payload = vector_bytes(vector)
        metadata = _metadata(identifier, payload)
        self.write_warnings: dict[str, str] = {}
        legacy = os.environ.get("CLIPOTECA_M21_SIDECARS", "1") != "0"
        m21_ok = False
        m22_ok = False
        if legacy:
            try:
                path = self.root / metadata["path"]
                if path.exists() and path.read_bytes() != payload:
                    raise EmbeddingError("immutable_clip_embedding_collision")
                index = self.index()
                prior = index["vectors"].get(identifier)
                if prior is not None and prior != metadata:
                    raise EmbeddingError("embedding_index_collision")
                if not path.exists():
                    _atomic_write(path, payload)
                index["vectors"][identifier] = metadata
                _atomic_write(
                    self.index_file,
                    (
                        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    ).encode(),
                )
                m21_ok = True
            except Exception:
                self.write_warnings["m21"] = "m21_sidecar_persist_failed:clip_published"
        # The M22 writer is independent of M21. A failed M21 index cannot
        # prevent a valid shard from being published (or vice versa).
        from .shards import ShardStore

        try:
            ShardStore(self.root).put(identifier, vector)
            m22_ok = True
        except Exception:
            self.write_warnings["m22"] = "m22_shard_dual_write_failed:m21_sidecar_persisted"
        if self.write_warnings:
            import warnings

            for code in self.write_warnings.values():
                warnings.warn(code, RuntimeWarning, stacklevel=2)
        if not m21_ok and not m22_ok:
            raise EmbeddingError("all_embedding_stores_failed")
        return metadata

    def read(self, identifier: str) -> np.ndarray:
        if not CLIP_ID_RE.fullmatch(identifier):
            raise ValueError("invalid clip identifier")
        index = self.index()
        metadata = index["vectors"].get(identifier)
        if not isinstance(metadata, dict) or metadata.get("path") != identifier + ".f16":
            raise EmbeddingError("embedding_not_indexed")
        payload = (self.root / metadata["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
            raise EmbeddingError("embedding_checksum_mismatch")
        return decode_vector(payload)


class GitHubEmbeddingWriter:
    """Publish sidecar+index to main only when actual ingest already publishes.

    The .f16 asset is immutable; an existing index is updated with SHA-based
    optimistic concurrency, mirroring the existing GitHub job/catalogue writers.
    """

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        branch: str = "main",
        client: httpx.Client | None = None,
    ) -> None:
        if not REPOSITORY_RE.fullmatch(repository) or branch != "main" or not token:
            raise ValueError("invalid embedding repository grant")
        self.repository = repository
        self.branch = branch
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=30,
            headers={
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        """Release only the internally-owned transport after the ingest run."""
        if self._owns_client:
            self.client.close()

    def _url(self, name: str) -> str:
        if name != "index.json" and not re.fullmatch(r"clip-[0-9]{6}\.f16", name):
            raise ValueError("unexpected embedding artifact")
        return f"https://api.github.com/repos/{self.repository}/contents/data/embeddings/{name}"

    def _read(self, name: str) -> tuple[bytes | None, str | None]:
        result = self.client.get(self._url(name), params={"ref": self.branch})
        if result.status_code == 404:
            return None, None
        result.raise_for_status()
        body = result.json()
        if body.get("encoding") != "base64" or not isinstance(body.get("content"), str):
            raise EmbeddingError("github_embedding_content_unavailable")
        sha = body.get("sha")
        if not isinstance(sha, str) or not sha:
            raise EmbeddingError("github_embedding_sha_missing")
        return base64.b64decode(body["content"]), sha

    def _put(self, name: str, payload: bytes, sha: str | None) -> bool:
        request = {
            "message": f"chore(embeddings): store {name}",
            "content": base64.b64encode(payload).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            request["sha"] = sha
        response = self.client.put(self._url(name), json=request)
        if response.status_code in (409, 422):
            return False
        response.raise_for_status()
        return True

    def publish(self, identifier: str, vector: np.ndarray) -> None:
        payload = vector_bytes(vector)
        metadata = _metadata(identifier, payload)
        current, _ = self._read(metadata["path"])
        if current is not None and current != payload:
            raise EmbeddingError("remote_immutable_embedding_collision")
        if current is None and not self._put(metadata["path"], payload, None):
            current, _ = self._read(metadata["path"])
            if current != payload:
                raise EmbeddingError("remote_immutable_embedding_conflict")
        for _ in range(4):
            current_index, sha = self._read("index.json")
            index = json.loads(current_index) if current_index else new_index()
            _check_index(index)
            prev = index["vectors"].get(identifier)
            if prev is not None and prev != metadata:
                raise EmbeddingError("remote_embedding_index_collision")
            if prev == metadata:
                return
            index["vectors"][identifier] = metadata
            data = (json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
            if self._put("index.json", data, sha):
                return
        raise EmbeddingError("remote_embedding_index_conflict")
