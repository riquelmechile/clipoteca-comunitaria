"""Offline catalog shortlist and deterministic no-reuse scene assignment."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

from .windows import fit_mode


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", value) if unicodedata.category(char) != "Mn"
    )


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", _fold(text))


def _values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, dict):
        return [text for key in ("es", "en") for text in _values(value.get(key))]
    return []


def _valid_clip(clip: object) -> bool:
    if not isinstance(clip, dict) or not isinstance(clip.get("id"), str):
        return False
    media, storage = clip.get("media"), clip.get("storage")
    if not isinstance(media, dict) or not isinstance(storage, dict):
        return False
    required_media = {
        "duration_seconds",
        "width",
        "height",
        "fps",
        "aspect_ratio",
        "orientation",
        "codec",
        "container",
        "has_audio",
        "size_bytes",
        "sha256",
        "perceptual_hash",
    }
    if not required_media.issubset(media):
        return False
    if not isinstance(storage.get("asset_url"), str) or not storage["asset_url"]:
        return False
    for name in ("duration_seconds", "width", "height"):
        number = media.get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return False
        if not math.isfinite(float(number)) or number <= 0:
            return False
    if media["duration_seconds"] > 20:
        return False
    if not all(
        isinstance(media[key], str) and media[key]
        for key in (
            "aspect_ratio",
            "orientation",
            "codec",
            "container",
            "sha256",
            "perceptual_hash",
        )
    ):
        return False
    if isinstance(media["fps"], bool) or not isinstance(media["fps"], (int, float)):
        return False
    if not math.isfinite(float(media["fps"])) or not 0 < media["fps"] <= 60:
        return False
    if not isinstance(media["has_audio"], bool):
        return False
    if not isinstance(media["size_bytes"], int) or media["size_bytes"] <= 0:
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
    for clip in source:
        if not isinstance(clip, dict):
            invalid += 1
        elif clip.get("status") != "published":
            unpublished += 1
        elif not _valid_clip(clip):
            invalid += 1
        else:
            clips.append(clip)
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
        *([source.get("source_title", "")] if isinstance(source, dict) else []),
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
        if min(width, height) < minimum_side:
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
