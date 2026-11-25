"""Deterministic M28 scene budgets and offline job files."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .script import (
    SceneSpec,
    MAX_VIDEO_SECONDS,
    ScriptError,
    VideoScript,
    estimate_durations,
    parse_video_script,
    rendered_scene_length,
    script_id,
)

NEG = [
    "a photo of a person talking",
    "a photo of a woman",
    "a photo of a man",
    "a photo of food",
    "a photo of a room interior",
    "a screenshot with text",
    "a photo of a street with cars",
    "a photo of a cat",
    "a video game screenshot",
    "a cartoon",
]
_SAFE_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def _safe_scene_id(scene_id: str) -> None:
    if not isinstance(scene_id, str) or not _SAFE_ID.fullmatch(scene_id):
        raise ScriptError("scenes.id: identificador de carpeta inseguro")
    if ".." in scene_id or "/" in scene_id or "\\" in scene_id:
        raise ScriptError("scenes.id: identificador de carpeta inseguro")


def estimate_scenes(script: VideoScript) -> list[dict[str, Any]]:
    """Quantize M25 speech estimates to the beat before any clip fetching."""
    bpm = float(script.music.bpm)
    beat = 60 / bpm
    durations = estimate_durations(script)["scenes"]
    result: list[dict[str, Any]] = []
    started = 0.0
    for scene in script.scenes:
        duration = durations[scene.id]
        length = rendered_scene_length(duration, scene.type, beat)
        rounded = round(length, 3)
        result.append(
            {
                "id": scene.id,
                **(
                    {
                        "source_kind": "catalog_pinned"
                        if str(scene.media["ref"]).startswith("clip-")
                        else "repo_asset"
                        if str(scene.media["ref"]).startswith("asset:")
                        else "contribution"
                    }
                    if scene.media
                    else {}
                ),
                "type": scene.type,
                "voice_est": duration,
                "len_est": rounded,
                "len_fetch": round(rounded + max(1.0, 0.12 * rounded), 2),
                "start_est": round(started, 3),
            }
        )
        started += rounded
    return result


def build_plan(script: VideoScript) -> dict[str, Any]:
    """M27-compatible plan schema, with no network or file access."""
    scenes = estimate_scenes(script)
    total = round(sum(scene["len_est"] for scene in scenes), 2)
    if total > MAX_VIDEO_SECONDS:
        raise ScriptError(
            f"scenes: el guion dura unos {total:g} s y el máximo es {MAX_VIDEO_SECONDS:g} s; "
            "acorta el texto o quita escenas"
        )
    return {
        "version": 1,
        "script_id": script_id(script),
        "title": script.title,
        "aspect": script.aspect,
        "language": script.language,
        "music": {"mood": script.music.mood, "bpm": script.music.bpm},
        "voice": {"voice": script.voice.voice, "speed": script.voice.speed},
        "bpm": float(script.music.bpm),
        "beat": 60 / float(script.music.bpm),
        "total_est": total,
        "scenes": scenes,
    }


def scene_job(script: VideoScript, scene: SceneSpec, est: dict[str, Any]) -> dict[str, Any]:
    """Materialize a single scene's immutable search and matching contract."""
    _safe_scene_id(scene.id)
    return {
        "version": 1,
        "script_id": script_id(script),
        "id": scene.id,
        "type": scene.type,
        "title": scene.title,
        "sub": scene.sub,
        "say": scene.say,
        "caption": scene.caption,
        "aspect": script.aspect,
        "len_est": est["len_est"],
        "len_fetch": est["len_fetch"],
        "start_est": est["start_est"],
        **(
            {
                "media": dict(scene.media),
                "source_kind": est["source_kind"],
                "on_error": scene.on_error,
            }
            if scene.media
            else {}
        ),
        "queries": {
            **{lang: list(scene.query.get(lang, ())) for lang in ("es", "en")},
            **(
                {"related": {lang: list(terms) for lang, terms in scene.query["related"].items()}}
                if "related" in scene.query
                else {}
            ),
        },
        "prompt": scene.prompt,
        "avoid": list(scene.avoid),
        "negatives": NEG.copy(),
        "min_sim": 0.22,
        "min_prob": 0.5,
        "min_score": 0.0,
        "catalog_min_sim": 0.27,
        "top_n": 4,
        "min_short_side": 720,
    }


def _atomic_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_plan(
    script: VideoScript,
    out_dir: str | Path,
    catalog_path: str | Path | None = None,
    *,
    resolve_related: bool = False,
) -> dict[str, Any]:
    """Write plan and one job per scene, rejecting unsafe IDs before any write."""
    for scene in script.scenes:
        _safe_scene_id(scene.id)
    plan = build_plan(script)
    root = Path(out_dir)
    jobs = [
        scene_job(script, scene, est)
        for scene, est in zip(script.scenes, plan["scenes"], strict=True)
    ]
    free_jobs = [job for job in jobs if "media" not in job]
    if resolve_related and free_jobs:
        from .related import resolve_jobs
        from .related_cache import load_cache

        diagnostics = asyncio.run(resolve_jobs(free_jobs, persistent_cache=load_cache()))
        for job, diagnostic in zip(free_jobs, diagnostics, strict=True):
            estimate = next(row for row in plan["scenes"] if row["id"] == job["id"])
            job["related_diagnostic"] = diagnostic
            estimate["related_diagnostic"] = diagnostic
            # A single machine-readable line per scene, retained in Actions logs.
            print(
                "related " + json.dumps({"scene": job["id"], **diagnostic}, ensure_ascii=True),
                flush=True,
            )
    assignments = None
    if catalog_path is not None:
        from .library import assign_scenes, load_catalog

        catalog = load_catalog(catalog_path)
        reserved = {
            job["media"]["ref"] for job in jobs if job.get("source_kind") == "catalog_pinned"
        }
        free_catalog = {"clips": [clip for clip in catalog["clips"] if clip["id"] not in reserved]}
        listed = assign_scenes(free_jobs, free_catalog)
        for job in jobs:
            if "media" not in job:
                continue
            row = {"id": job["id"], "status": job["source_kind"]}
            if job["source_kind"] == "catalog_pinned":
                clip = next(
                    (clip for clip in catalog["clips"] if clip["id"] == job["media"]["ref"]), None
                )
                if clip is None:
                    raise ScriptError("clip fijado inexistente o retirado")
                row["primary"] = {
                    "clip_id": clip["id"],
                    "asset_url": clip["storage"]["asset_url"],
                    "sha256": clip["media"]["sha256"],
                    "size_bytes": clip["media"]["size_bytes"],
                    "source_url": clip["source"].get("source_url"),
                }
            listed.append(row)
        assignments = {
            "version": 1,
            "script_id": plan["script_id"],
            "scenes": {
                item["id"]: {
                    "status": item["status"],
                    "primary": item.get("primary"),
                    "backups": item.get("backups", []),
                }
                for item in listed
            },
        }
    for scene, job in zip(script.scenes, jobs, strict=True):
        folder = root / "scenes" / scene.id
        _atomic_json(folder / "job.json", job)
        if assignments is not None:
            _atomic_json(folder / "assignment.json", assignments["scenes"][scene.id])
    if assignments is not None:
        _atomic_json(root / "assignments.json", assignments)
    _atomic_json(root / "plan.json", plan)
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.plan")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--resolve-related", action="store_true")
    args = parser.parse_args(argv)
    try:
        script = parse_video_script(json.loads(args.script.read_text(encoding="utf-8")))
        plan = write_plan(script, args.out, args.catalog, resolve_related=args.resolve_related)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"plan: {exc}\n")
    print(
        json.dumps(
            {
                "script_id": plan["script_id"],
                "scenes": [scene["id"] for scene in plan["scenes"]],
                "total_est": plan["total_est"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
