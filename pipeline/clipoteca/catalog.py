"""Canonical catalogue, strict asset verification and collision-safe GitHub persistence."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import tempfile
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

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


_SECRET_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "auth_token",
        "client_secret",
        "password",
        "secret",
        "token",
        "key",
    }
)

# These providers authenticate with ?key=. On other hosts it may identify an
# asset (e.g. a public media variant), so canonical identity must retain it.
_CREDENTIAL_KEY_HOSTS = frozenset(
    {"api.data.gov", "pixabay.com", "googleapis.com", "youtube.googleapis.com"}
)

_TRACKING_QUERY_KEYS = {
    "_hsenc",
    "_hsmi",
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}


def canonical_source_url(value: str, *, redact_secrets: bool = False) -> str:
    """Keep identity-bearing query params; remove only known tracking keys.

    In particular, 'si' and 'feature' are retained unless a provider-specific
    contract proves them to be tracking-only. Conservative false negatives
    are safer than collapsing distinct assets.
    """
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    credential_key_host = host in _CREDENTIAL_KEY_HOSTS
    try:
        port = parsed.port
    except ValueError:
        return ""
    if ":" in host:
        host = f"[{host}]"
    if port and port not in {80, 443}:
        host = f"{host}:{port}"
    path = quote(unquote(parsed.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if (
            not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_QUERY_KEYS
            and (
                key.casefold() not in _SECRET_QUERY_KEYS
                or (key.casefold() == "key" and not credential_key_host and not redact_secrets)
            )
        )
    ]
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def safe_source_url(value: str) -> str:
    """Preserve public source URLs while removing embedded API credentials.

    Identity parameters (including si and feature) remain untouched.
    """
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return canonical_source_url(value, redact_secrets=True)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        safe = [(k, v) for k, v in query if k.casefold() not in _SECRET_QUERY_KEYS]
        if len(safe) == len(query):
            return value
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(safe), parsed.fragment)
        )
    except ValueError:
        return ""


def normalized_source_title(value: str) -> str:
    """Normalize case, accents and punctuation for cross-provider comparison."""
    return " ".join(re.findall(r"\w+", normalize_text(value), flags=re.UNICODE))


def _distinctive_title(value: str) -> bool:
    """Avoid collapsing generic stock labels such as ``rain`` across providers."""
    words = value.split()
    return len(words) >= 2 and len(value) >= 12


def canonical_tokens(value: str) -> set[str]:
    words = re.findall(r"\w+", normalize_text(value), flags=re.UNICODE)
    tokens = set(words)
    for word in words:
        # CJK words have no whitespace: index all contiguous two-codepoint windows.
        if any("\u3400" <= c <= "\u9fff" for c in word):
            tokens.update(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def _nonoverlapping_source_windows(source: dict, published: dict) -> bool:
    """Same source can represent distinct clips only with explicit disjoint ranges."""
    try:
        first = float(source["source_start"])
        first_end = float(source["source_end"])
        second = float(published["source_start"])
        second_end = float(published["source_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return all(math.isfinite(t) for t in (first, first_end, second, second_end)) and (
        first_end <= second or second_end <= first
    )


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

    def duplicate_match(
        self,
        candidate: dict[str, Any],
        *,
        phash_similar: Callable[[str, str], bool] | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """Require a strong content/URL signal, not title similarity alone.

        Independent duplicates: same provider identity, same canonical URL
        (even if provider_id changed), SHA-256 match, or close pHash.
        A cross-provider title match needs secondary evidence: duration
        within one second, or identical explicit creator/collection.
        """
        source = candidate.get("source", {})
        media = candidate.get("media", {})
        candidate_url = canonical_source_url(str(source.get("source_url", "")))
        candidate_title = normalized_source_title(str(source.get("source_title", "")))
        for clip in self.clips:
            published_source = clip["source"]
            published_media = clip["media"]
            if (
                published_source["provider"] == source.get("provider")
                and published_source["provider_id"] == source.get("provider_id")
                and not _nonoverlapping_source_windows(source, published_source)
            ):
                return clip, "provider_id"
            if media.get("sha256") and published_media.get("sha256") == media["sha256"]:
                return clip, "sha256"
            published_url = canonical_source_url(str(published_source.get("source_url", "")))
            if (
                candidate_url
                and published_url
                and candidate_url == published_url
                and not _nonoverlapping_source_windows(source, published_source)
            ):
                return clip, "canonical_url"
            same_origin = published_source["provider"] == source.get("provider") and (
                published_source["provider_id"] == source.get("provider_id")
                or (candidate_url and published_url and candidate_url == published_url)
            )
            same_origin_disjoint = same_origin and _nonoverlapping_source_windows(
                source, published_source
            )
            if (
                phash_similar
                and not same_origin_disjoint
                and media.get("perceptual_hash")
                and published_media.get("perceptual_hash")
                and phash_similar(published_media["perceptual_hash"], media["perceptual_hash"])
            ):
                return clip, "perceptual_hash"
            if (
                published_source["provider"] != source.get("provider")
                and _distinctive_title(candidate_title)
                and normalized_source_title(str(published_source.get("source_title", "")))
                == candidate_title
            ):
                # Missing duration or metadata never counts as corroboration.
                current_duration = media.get("duration_seconds")
                previous_duration = published_media.get("duration_seconds")
                duration_match = (
                    isinstance(current_duration, (int, float))
                    and not isinstance(current_duration, bool)
                    and isinstance(previous_duration, (int, float))
                    and not isinstance(previous_duration, bool)
                    and math.isfinite(current_duration)
                    and math.isfinite(previous_duration)
                    and abs(current_duration - previous_duration) <= 1
                )

                def same_field(key: str) -> bool:
                    value = source.get(key)
                    return bool(
                        isinstance(value, str)
                        and value.strip()
                        and isinstance(published_source.get(key), str)
                        and normalize_text(value) == normalize_text(published_source[key])
                    )

                if duration_match or same_field("creator") or same_field("collection"):
                    return clip, "normalized_title"
        return None

    def find_duplicate(
        self,
        candidate: dict[str, Any],
        *,
        phash_similar: Callable[[str, str], bool] | None = None,
    ) -> dict[str, Any] | None:
        match = self.duplicate_match(candidate, phash_similar=phash_similar)
        return match[0] if match else None

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
        verified_assets: Mapping[str, tuple[ReleaseAsset, ...]] | None = None,
        phash_similar: Callable[[str, str], bool] | None = None,
        retries: int = 3,
    ) -> str:
        clips = list(incoming)
        if not clips or not verified_assets:
            raise ValueError("publication requires clips and verified video/thumbnail assets")
        for clip in clips:
            self.validator.validate(clip)
            pair = verified_assets.get(clip["id"])
            if pair is None or len(pair) < 2:
                raise ValueError(f"missing verified assets for {clip['id']}")
            video, thumb, *storyboard = pair
            expected = clip["storage"]
            story_urls = expected.get("storyboard_urls", [])
            if len(storyboard) != len(story_urls):
                raise ValueError("storyboard assets must be verified before publication")
            for index, (preview, url) in enumerate(zip(storyboard, story_urls, strict=True)):
                if (
                    preview.state != "uploaded"
                    or preview.size <= 0
                    or preview.browser_download_url != url
                    or preview.name != f"story_{clip['id']}_{index:02d}.webp"
                ):
                    raise ValueError("storyboard asset verification mismatch")
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
