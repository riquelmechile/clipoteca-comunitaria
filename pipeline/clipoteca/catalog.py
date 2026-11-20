"""Canonical catalogue, deduplication and GitHub Contents persistence."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator


def canonical_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return {
        token for token in re.split(r"[^\w]+", normalized, flags=re.UNICODE) if token
    }


class Catalog:
    def __init__(self, clips: Iterable[dict[str, Any]] = ()) -> None:
        self.clips = sorted((dict(clip) for clip in clips), key=lambda clip: clip["id"])

    @classmethod
    def load(cls, path: Path) -> Catalog:
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.clips, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def next_id(self) -> str:
        largest = max(
            (int(item["id"].removeprefix("clip-")) for item in self.clips), default=0
        )
        return f"clip-{largest + 1:06d}"

    def merge(self, incoming: Iterable[dict[str, Any]]) -> None:
        merged = {item["id"]: item for item in self.clips}
        merged.update({item["id"]: item for item in incoming})
        self.clips = sorted(merged.values(), key=lambda item: item["id"])

    def find_duplicate(
        self,
        candidate: dict[str, Any],
        *,
        phash_similar: Callable[[str, str], bool] | None = None,
    ) -> dict[str, Any] | None:
        source = candidate["source"]
        media = candidate["media"]
        for clip in self.clips:
            if (clip["source"]["provider"], clip["source"]["provider_id"]) == (
                source["provider"],
                source["provider_id"],
            ):
                return clip
            if media.get("sha256") and clip["media"].get("sha256") == media["sha256"]:
                return clip
            if (
                phash_similar
                and media.get("perceptual_hash")
                and clip["media"].get("perceptual_hash")
                and phash_similar(
                    clip["media"]["perceptual_hash"], media["perceptual_hash"]
                )
            ):
                return clip
        return None

    def add_verified(
        self, clip: dict[str, Any], schema: dict[str, Any], *, asset_verified: bool
    ) -> None:
        if not asset_verified:
            raise ValueError("catalogue update requires a verified release asset")
        Draft202012Validator(schema).validate(clip)
        if self.find_duplicate(clip):
            return
        self.merge([clip])

    def indexes(self) -> dict[str, dict[str, list[str]]]:
        indexes: dict[str, dict[str, list[str]]] = {"es": {}, "en": {}}
        for clip in self.clips:
            original = clip["source"].get("source_title", "")
            for language in ("es", "en"):
                strings = [clip["title"][language], clip["description"][language]]
                strings.extend(clip["tags"][language])
                strings.append(original)
                for token in set().union(
                    *(canonical_tokens(value) for value in strings)
                ):
                    indexes[language].setdefault(token, []).append(clip["id"])
        return indexes

    def write_indexes(self, root: Path) -> None:
        for language, mapping in self.indexes().items():
            target = root / language
            target.mkdir(parents=True, exist_ok=True)
            for token, ids in mapping.items():
                (target / f"{token}.json").write_text(
                    json.dumps(sorted(set(ids)), ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )


class GitHubCatalogWriter:
    """Optimistic Contents API writer with bounded 409 merge retries."""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        schema: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.client = client or httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        schema_path = Path("schemas/clip.schema.json")
        self.schema = schema or (
            json.loads(schema_path.read_text(encoding="utf-8"))
            if schema_path.exists()
            else None
        )

    def update(self, incoming: Iterable[dict[str, Any]], *, retries: int = 3) -> str:
        url = f"https://api.github.com/repos/{self.repository}/contents/data/clips.json"
        import base64

        incoming = list(incoming)
        if self.schema is not None:
            validator = Draft202012Validator(self.schema)
            for clip in incoming:
                validator.validate(clip)
        for attempt in range(retries + 1):
            current = self.client.get(url)
            current.raise_for_status()
            payload = current.json()
            content = base64.b64decode(payload["content"])
            catalog = Catalog(json.loads(content))
            catalog.merge(incoming)
            encoded = base64.b64encode(
                (
                    json.dumps(catalog.clips, ensure_ascii=False, indent=2) + "\n"
                ).encode()
            ).decode()
            response = self.client.put(
                url,
                json={
                    "message": "chore(catalog): publish verified clip",
                    "content": encoded,
                    "sha": payload["sha"],
                },
            )
            if response.status_code != 409:
                response.raise_for_status()
                return str(response.json()["commit"]["sha"])
            if attempt == retries:
                raise RuntimeError(
                    "catalogue remained conflicted after bounded retries"
                )
        raise AssertionError("unreachable")
