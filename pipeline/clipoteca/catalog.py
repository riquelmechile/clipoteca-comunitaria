"""Canonical catalogue, strict asset verification and collision-safe GitHub persistence."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from .storage.github import ReleaseAsset
from .translate.dictionary import SYNONYMS, find_concept, normalize

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "clip.schema.json"


class IdentityCollision(ValueError):
    """An existing identifier belongs to a different video."""


class DuplicateClip(ValueError):
    """An already published source or video must not be uploaded again."""

    def __init__(self, clip_id: str) -> None:
        self.clip_id = clip_id
        super().__init__(f"duplicate clip: {clip_id}")


def normalize_text(value: str) -> str:
    compatibility = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        c for c in unicodedata.normalize("NFKD", compatibility) if unicodedata.category(c) != "Mn"
    )


def canonical_tokens(value: str) -> set[str]:
    words = re.findall(r"\w+", normalize_text(value), flags=re.UNICODE)
    tokens = set(words)
    for word in words:
        # CJK words have no whitespace: index all contiguous two-codepoint windows.
        if any("\u3400" <= c <= "\u9fff" for c in word):
            tokens.update(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


class Catalog:
    def __init__(self, clips: Iterable[dict[str, Any]] = ()) -> None:
        self.clips = sorted((dict(clip) for clip in clips), key=lambda c: c["id"])

    @classmethod
    def load(cls, path: Path) -> Catalog:
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        """Replace the catalogue atomically; never leave truncated JSON on disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".clips-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp:
                temp.write(json.dumps(self.clips, ensure_ascii=False, indent=2) + "\n")
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(tmp_name, path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def next_id(self, *, reserved_ids: set[str] | None = None) -> str:
        largest = max((int(item["id"].removeprefix("clip-")) for item in self.clips), default=0)
        excluded = reserved_ids or set()
        while True:
            largest += 1
            candidate = f"clip-{largest:06d}"
            if candidate not in excluded:
                return candidate

    def merge(self, incoming: Iterable[dict[str, Any]]) -> None:
        merged = {item["id"]: item for item in self.clips}
        for item in incoming:
            old = merged.get(item["id"])
            if old is not None and old != item:
                raise IdentityCollision(item["id"])
            merged[item["id"]] = item
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
                and phash_similar(clip["media"]["perceptual_hash"], media["perceptual_hash"])
            ):
                return clip
        return None

    def add_verified(
        self, clip: dict[str, Any], schema: dict[str, Any], *, asset_verified: bool
    ) -> None:
        if not asset_verified:
            raise ValueError("catalogue update requires verified release assets")
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(clip)
        if self.find_duplicate(clip):
            return
        self.merge([clip])

    def indexes(self) -> dict[str, dict[str, list[str]]]:
        indexes: dict[str, dict[str, list[str]]] = {"es": {}, "en": {}}
        for clip in self.clips:
            original = clip["source"].get("source_title", "")
            original_tags = clip["source"].get("keywords", [])
            if not isinstance(original_tags, list):
                original_tags = []
            for language in ("es", "en"):
                strings = [clip["title"][language], clip["description"][language]]
                strings.extend(clip["tags"][language])
                strings.extend(original_tags)
                strings.append(original)
                # Index all known equivalents and synonyms, including original
                # Russian/Chinese text, in both ES and EN token maps.
                for phrase in list(strings):
                    concept = find_concept(str(phrase))
                    if concept:
                        strings.extend(concept.terms.values())
                        strings.extend(
                            term
                            for group in SYNONYMS.get(normalize(concept.terms["es"]), {}).values()
                            for term in group
                        )
                for token in set().union(*(canonical_tokens(v) for v in strings)):
                    indexes[language].setdefault(token, []).append(clip["id"])
        return indexes

    def write_indexes(self, root: Path) -> None:
        for language, mapping in self.indexes().items():
            target = root / language
            target.mkdir(parents=True, exist_ok=True)
            filenames: set[str] = set()
            for token, ids in mapping.items():
                safe = token
                if len(safe.encode("utf-8")) > 180:
                    safe = hashlib.sha256(token.encode("utf-8")).hexdigest()
                name = f"{safe}.json"
                filenames.add(name)
                (target / name).write_text(
                    json.dumps(sorted(set(ids)), ensure_ascii=False) + "\n", encoding="utf-8"
                )
            for old in target.glob("*.json"):
                if old.name not in filenames:
                    old.unlink()


class GitHubCatalogWriter:
    """Read the current remote state before each optimistic commit attempt."""

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
        if schema is None:
            if not SCHEMA_PATH.is_file():
                raise FileNotFoundError(f"catalogue schema missing: {SCHEMA_PATH}")
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    @property
    def url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/contents/data/clips.json"

    def read_catalog(self) -> tuple[Catalog, str]:
        current = self.client.get(self.url)
        current.raise_for_status()
        payload = current.json()
        if not isinstance(payload, dict) or not payload.get("sha"):
            raise ValueError("GitHub did not return an optimistic catalogue SHA")
        raw: bytes
        if payload.get("encoding") == "none" or not payload.get("content"):
            response = self.client.get(self.url, headers={"Accept": "application/vnd.github.raw"})
            response.raise_for_status()
            raw = response.content
        else:
            if payload.get("encoding", "base64") != "base64":
                raise ValueError("unknown catalogue content encoding")
            raw = base64.b64decode(re.sub(r"\s+", "", payload["content"]), validate=True)
        if not raw.strip():
            raise ValueError("empty remote catalogue, refusing overwrite")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("remote catalogue must be a JSON array")
        for clip in data:
            self.validator.validate(clip)
        return Catalog(data), str(payload["sha"])

    def update(
        self,
        incoming: Iterable[dict[str, Any]],
        *,
        verified_assets: Mapping[str, tuple[ReleaseAsset, ReleaseAsset]] | None = None,
        phash_similar: Callable[[str, str], bool] | None = None,
        retries: int = 3,
    ) -> str:
        clips = list(incoming)
        if not clips or not verified_assets:
            raise ValueError("publication requires clips and verified video/thumbnail assets")
        for clip in clips:
            self.validator.validate(clip)
            pair = verified_assets.get(clip["id"])
            if pair is None or len(pair) != 2:
                raise ValueError(f"missing verified assets for {clip['id']}")
            video, thumb = pair
            expected = clip["storage"]
            if (
                video.state != "uploaded"
                or thumb.state != "uploaded"
                or video.size <= 0
                or thumb.size <= 0
                or video.browser_download_url != expected["asset_url"]
                or thumb.browser_download_url != expected["thumbnail_url"]
                or video.name != f"clip_{clip['id']}.mp4"
                or thumb.name != f"thumb_{clip['id']}.webp"
            ):
                raise ValueError("verified release asset pair does not match catalogue URLs/names")
        for attempt in range(retries + 1):
            catalog, sha = self.read_catalog()
            for clip in clips:
                duplicate = catalog.find_duplicate(clip, phash_similar=phash_similar)
                if duplicate is not None:
                    raise DuplicateClip(duplicate["id"])
            catalog.merge(clips)
            encoded = base64.b64encode(
                (json.dumps(catalog.clips, ensure_ascii=False, indent=2) + "\n").encode()
            ).decode()
            response = self.client.put(
                self.url,
                json={
                    "message": "chore(catalog): publish verified clip",
                    "content": encoded,
                    "sha": sha,
                },
            )
            if response.status_code == 409:
                if attempt == retries:
                    raise RuntimeError("catalogue conflict after bounded retries")
                continue
            response.raise_for_status()
            return str(response.json()["commit"]["sha"])
        raise AssertionError("unreachable")
