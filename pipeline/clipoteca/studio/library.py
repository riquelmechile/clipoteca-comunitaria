"""Offline catalog shortlist and deterministic no-reuse scene assignment."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from ..place_names import _fold, _words, aliases, query_names
from .windows import fit_mode


def _values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return [text for key in ("es", "en") for text in _values(value.get(key))]
    return []


_STOP = frozenset(
    "a an the of in on at and or with from for to is by as footage video view "
    "el la los las de del en y o con por para un una al a que es vista imagen".split()
)


def _source_fragments(source):
    return (
        _values(source.get("source_title"))
        + _values(source.get("commons_category"))
        + _values(source.get("commons_place_aliases"))
    )


def name_match(clip, queries, *, source_only=False):
    names = query_names(queries)
    if not names:
        return False
    fragments = _values(clip.get("title"))
    if not source_only:
        fragments += _values(clip.get("description")) + _values(clip.get("tags"))
    source = clip.get("source") or {}
    fragments += _source_fragments(source)
    return any(
        f" {alias} " in f" {' '.join(_words(fragment))} "
        for name in names
        for alias in aliases(name)
        for fragment in fragments
    )


def sufficient_query_match(clip, queries):
    source = clip.get("source") or {}
    fragments = _values(clip.get("title")) + _source_fragments(source)
    tokens = {word for fragment in fragments for word in _words(fragment)}
    names = query_names(queries)
    if names and not name_match(clip, queries, source_only=True):
        return False
    for query in queries:
        terms = set(_words(query)) - _STOP
        required = 1 if len(terms) <= 2 else 2
        if terms and len(terms & tokens) >= required:
            return True
    return False


def _valid_clip(clip: object) -> bool:
    if not isinstance(clip, dict) or not isinstance(clip.get("id"), str):
        return False
    media, storage = clip.get("media"), clip.get("storage")
    if not isinstance(media, dict) or not isinstance(storage, dict):
        return False
    required_media = {"duration_seconds", "width", "height", "sha256", "size_bytes"}
    if not required_media.issubset(media):
        return False
    if not isinstance(storage.get("asset_url"), str) or not storage["asset_url"]:
        return False
    for key in ("duration_seconds", "width", "height"):
        number = media.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return False
        if not math.isfinite(float(number)) or number <= 0:
            return False
    if not isinstance(media["width"], int) or not isinstance(media["height"], int):
        return False
    size = media["size_bytes"]
    digest = media["sha256"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return False
    if not isinstance(digest, str) or re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None:
        return False
    return True


def load_catalog(path: str | Path) -> dict[str, Any]:
    """Load published clips, skipping and counting malformed entries without network."""
    try:
        source = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read clip catalog: {exc}") from exc
    if isinstance(source, dict):
        source = source.get("clips")
    if not isinstance(source, list):
        raise ValueError("clip catalog must be an array")
    clips: list[dict[str, Any]] = []
    invalid = 0
    unpublished = 0
    seen: set[str] = set()
    for clip in source:
        if not isinstance(clip, dict):
            invalid += 1
        elif clip.get("status") != "published":
            unpublished += 1
        elif not _valid_clip(clip) or clip["id"] in seen:
            invalid += 1
        else:
            clips.append(clip)
            seen.add(clip["id"])
    clips.sort(key=lambda item: item["id"])
    return {"clips": clips, "invalid": invalid, "unpublished": unpublished}


def text_score(
    clip: dict[str, Any],
    queries_es: list[str],
    queries_en: list[str],
    avoid: list[str],
) -> tuple[float, list[str]]:
    """Whole-word matches in both languages; avoid phrases cost six points."""
    terms = {
        word for query in [*queries_es, *queries_en] for word in _words(query) if len(word) >= 4
    }
    source = clip.get("source", {})
    fragments = [
        *_values(clip.get("title")),
        *_values(clip.get("description")),
        *_values(clip.get("tags")),
        *(_source_fragments(source) if isinstance(source, dict) else []),
    ]
    haystack = _fold(" ".join(text for text in fragments if isinstance(text, str)))
    tokens = set(_words(haystack))
    matched = sorted(terms & tokens)
    penalties = sum(
        6
        for value in avoid
        if isinstance(value, str)
        and value.strip()
        and re.search(r"(?<!\w)" + re.escape(_fold(value.strip())) + r"(?!\w)", haystack)
    )
    return 0.5 * len(matched) - penalties, matched


def _clips(catalog: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(catalog, dict):
        return catalog.get("clips", [])
    return catalog


def candidates(
    job: dict[str, Any],
    catalog: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter by media quality, estimated fit and whole-word bilingual relevance."""
    length = float(job["len_est"])
    minimum_side = int(job.get("min_short_side", 720))
    queries = job.get("queries", {})
    scored = []
    for clip in _clips(catalog):
        if not _valid_clip(clip) or clip.get("status") != "published":
            continue
        media = clip["media"]
        duration = float(media["duration_seconds"])
        if duration < 0.42 * length:
            continue
        width, height = int(media["width"]), int(media["height"])
        aspect = job.get("aspect")
        if aspect in {"9:16", "16:9", "1:1"}:
            from .framing import resolution_ok, target_size

            target_width, target_height = target_size(aspect)
            if not resolution_ok(
                width,
                height,
                target_width,
                target_height,
                minimum_side,
            ):
                continue
        elif min(width, height) < minimum_side:
            continue
        score, matched = text_score(
            clip,
            queries.get("es", []),
            queries.get("en", []),
            job.get("avoid", []),
        )
        if score <= 0:
            continue
        fit = fit_mode(duration, length)
        if fit is None:
            continue
        source = clip.get("source", {})
        scored.append(
            {
                "clip_id": clip["id"],
                "score": score,
                "matched": matched,
                "duration": duration,
                "width": width,
                "height": height,
                "asset_url": clip["storage"]["asset_url"],
                "sha256": media["sha256"],
                "size_bytes": media["size_bytes"],
                "source_url": source.get("source_url") if isinstance(source, dict) else None,
                "fit": fit,
            }
        )
    return sorted(scored, key=lambda item: (-item["score"], item["clip_id"]))


def assign_scenes(
    jobs: list[dict[str, Any]],
    catalog: dict[str, Any] | list[dict[str, Any]],
    top_n: int = 4,
) -> list[dict[str, Any]]:
    """Prioritize hardest scenes first; never repeat a primary clip."""
    ranked = [candidates(job, catalog) for job in jobs]
    order = sorted(range(len(jobs)), key=lambda i: (len(ranked[i]), i))
    primary: dict[int, dict[str, Any]] = {}
    occupied: set[str] = set()
    for index in order:
        for candidate in ranked[index]:
            if candidate["clip_id"] not in occupied:
                primary[index] = candidate
                occupied.add(candidate["clip_id"])
                break
    result = []
    for index, job in enumerate(jobs):
        if index not in primary:
            result.append({"id": job["id"], "status": "needs_import"})
            continue
        lead = primary[index]
        backups = [
            candidate
            for candidate in ranked[index]
            if candidate["clip_id"] != lead["clip_id"] and candidate["clip_id"] not in occupied
        ][: max(0, top_n - 1)]
        result.append(
            {
                "id": job["id"],
                "status": "catalog",
                "primary": lead,
                "backups": backups,
            }
        )
    return result
