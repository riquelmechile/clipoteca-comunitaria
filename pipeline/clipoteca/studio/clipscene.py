"""Per-scene local CLIP selection, verified asset fetch, cover transcode and QA."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import checks, framing
from .windows import choose_window, fit_mode, windows


class ClipSceneError(ValueError):
    """Invalid job or an unavailable video-processing dependency."""


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _valid_job(job: Any, assignment: Any) -> tuple[str, float]:
    if not isinstance(job, dict) or not isinstance(assignment, dict):
        raise ClipSceneError("job and assignment must be JSON objects")
    scene_id = job.get("id")
    if not isinstance(scene_id, str) or not _SAFE_ID.fullmatch(scene_id) or ".." in scene_id:
        raise ClipSceneError("job.id is unsafe")
    try:
        length = float(job["len_fetch"])
        framing.target_size(job["aspect"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ClipSceneError("job.len_fetch or job.aspect is invalid") from exc
    if not math.isfinite(length) or length <= 0:
        raise ClipSceneError("job.len_fetch must be positive")
    status = assignment.get("status")
    if status not in {"catalog", "needs_import"}:
        raise ClipSceneError("assignment.status must be catalog or needs_import")
    for field in ("backups",):
        if field in assignment and not isinstance(assignment[field], list):
            raise ClipSceneError(f"assignment.{field} must be an array")
    return scene_id, length


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _verify(path: Path, candidate: dict[str, Any]) -> bool:
    digest = candidate.get("sha256")
    size = candidate.get("size_bytes")
    if not isinstance(digest, str) or re.fullmatch(r"[a-fA-F0-9]{64}", digest) is None:
        return False
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return False
    if path.stat().st_size != size:
        return False
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hashed.update(block)
    return hashed.hexdigest().lower() == digest.lower()


def _download(candidate: dict[str, Any], path: Path, fetch: Any) -> None:
    if fetch is None:
        from . import models

        models.download_asset(
            {
                "name": candidate["clip_id"],
                "size": candidate["size_bytes"],
                "sha256": candidate["sha256"],
                "urls": [candidate["asset_url"]],
            },
            path,
        )
    else:
        fetch(
            {
                "name": candidate["clip_id"],
                "size": candidate["size_bytes"],
                "sha256": candidate["sha256"],
                "urls": [candidate["asset_url"]],
            },
            path,
        )
    if not path.is_file() or not _verify(path, candidate):
        raise ClipSceneError("asset checksum or size mismatch")


def _run(args: list[str], deadline: float) -> subprocess.CompletedProcess[str]:
    timeout = min(120.0, deadline - time.monotonic())
    if timeout <= 0:
        raise TimeoutError("scene timeout")
    try:
        process = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("scene timeout") from exc
    except OSError as exc:
        raise ClipSceneError(f"media process unavailable: {type(exc).__name__}") from exc
    if process.returncode:
        raise ClipSceneError("media process failed: " + process.stderr[-180:])
    return process


def _preview_frames(
    path: Path,
    sw: int,
    sh: int,
    duration: float,
    deadline: float,
) -> tuple[list[np.ndarray], np.ndarray]:
    """Bounded binary 2 fps frame decode; never a model or network request."""
    width = 384
    height = max(2, round((sh / sw * width) / 2) * 2)
    seconds = min(duration, 120.0)
    timeout = min(120.0, deadline - time.monotonic())
    if timeout <= 0:
        raise TimeoutError("scene timeout")
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-t",
                f"{seconds:.5f}",
                "-vf",
                f"fps=2,scale={width}:{height},format=bgr24",
                "-an",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("scene timeout") from exc
    except OSError as exc:
        raise ClipSceneError("preview ffmpeg missing") from exc
    if result.returncode:
        raise ClipSceneError("preview ffmpeg failed")
    frame_bytes = width * height * 3
    if not result.stdout or len(result.stdout) % frame_bytes:
        raise ClipSceneError("preview frame stream incomplete")
    count = len(result.stdout) // frame_bytes
    frames = [
        frame.copy()
        for frame in np.frombuffer(result.stdout, dtype=np.uint8).reshape(count, height, width, 3)
    ]
    return frames, np.arange(count, dtype=np.float32) / 2


def _cuts(path: Path, deadline: float) -> list[float]:
    process = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            r"select=gt(scene\,0.3),showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        deadline,
    )
    return [float(value) for value in re.findall(r"pts_time:\s*([\d.]+)", process.stderr)]


def _ocr_positions(
    path: Path,
    start: float,
    length: float,
    duration: float,
    fractions: tuple[float, ...],
    ocr: Any,
) -> bool:
    import cv2

    values = [min(0.99, max(0.0, (start + ratio * length) / duration)) for ratio in fractions]
    frames = checks.sample_frames(path, values, width=720)
    doubled = [
        cv2.resize(frame, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR) for frame in frames
    ]
    return bool(checks.screen_text(doubled, ocr=ocr))


def _make_focus(
    frames: list[np.ndarray],
    ts: np.ndarray,
    start: float,
    length: float,
) -> tuple[float, float]:
    import cv2
    from PIL import Image

    subset = [
        frame
        for frame, time_stamp in zip(frames, ts, strict=True)
        if start <= time_stamp < start + length
    ]
    if not subset:
        return (0.5, 0.5)
    selected = [subset[i] for i in np.linspace(0, len(subset) - 1, 4, dtype=int)]
    focuses = [
        framing.saliency_focus(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        for frame in selected
    ]
    return (
        float(np.median([f[0] for f in focuses])),
        float(np.median([f[1] for f in focuses])),
    )


def _attempt(
    job: dict[str, Any],
    candidate: dict[str, Any],
    downloaded: Path,
    rendered: Path,
    scorer: Any,
    ocr: Any,
    deadline: float,
) -> tuple[dict[str, Any] | None, str]:
    length = float(job["len_fetch"])
    try:
        sw, sh, duration = checks.probe_video(downloaded)
        width, height = framing.target_size(job["aspect"])
        if not framing.resolution_ok(
            sw,
            sh,
            width,
            height,
            int(job.get("min_short_side", 720)),
        ):
            return None, "low_resolution"
        mode = fit_mode(duration, length)
        if mode is None:
            return None, "too_short"
        frames, stamps = _preview_frames(downloaded, sw, sh, duration, deadline)
        if not frames:
            return None, "no_window"
        sharp, bright, shift, diff = scorer.frame_metrics(frames)
        embedding = scorer.image_emb(frames)
        prompt = scorer.text_emb([job["prompt"]])[0]
        negatives = scorer.text_emb(job.get("negatives", []))
        analyzed = {
            "emb": embedding,
            "ts": stamps,
            "dur": duration,
            "sharp": sharp,
            "bright": bright,
            "shift": shift,
            "diff": diff,
            "cuts": _cuts(downloaded, deadline),
        }
        proposals = windows(analyzed, prompt, length, nemb=negatives)
        chosen, reason = choose_window(
            proposals, job.get("min_sim", 0.22), job.get("min_prob", 0.5)
        )
        if chosen is None:
            return None, reason
        begin = chosen["start"]
        actual_span = duration if mode == "whole" else length
        if _ocr_positions(downloaded, begin, actual_span, duration, (0.25, 0.75), ocr):
            return None, "text_in_preview"
        focus = _make_focus(frames, stamps, begin, actual_span)
        local_frames = [
            frame
            for frame, timestamp in zip(frames, stamps, strict=True)
            if begin <= timestamp < begin + actual_span
        ]
        samples = local_frames[:: max(1, len(local_frames) // 3)][:3]
        vf, crop = framing.crop_filter(
            sw,
            sh,
            job["aspect"],
            focus,
            frames=samples,
            prompt_embedding=prompt,
            scorer=scorer,
        )
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{begin:.5f}",
                "-i",
                str(downloaded),
                "-t",
                f"{length + 0.1:.5f}",
                "-an",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "19",
                "-movflags",
                "+faststart",
                str(rendered),
            ],
            deadline,
        )
        rw, rh, output_dur = checks.probe_video(rendered)
        if rw != width or rh != height:
            return None, "bad_resolution"
        if output_dur < max(2.0, 0.42 * length):
            return None, "short_output"
        black, frozen = checks.black_frozen(rendered)
        if black:
            return None, "black"
        if frozen:
            return None, "frozen"
        if _ocr_positions(rendered, 0.0, output_dur, output_dur, (0.2, 0.5, 0.8), ocr):
            return None, "on_screen_text"
        return {
            "clip_id": candidate["clip_id"],
            "source_url": candidate.get("source_url"),
            "asset_url": candidate["asset_url"],
            "start": begin,
            "len": length,
            "out_dur": round(output_dur, 3),
            "fit": mode,
            "score": chosen["score"],
            "sim": chosen["sim"],
            "prob": chosen["prob"],
            "crop": [round(float(x), 6) for x in crop],
            "src_w": sw,
            "src_h": sh,
            "out_w": rw,
            "out_h": rh,
            "phash": checks.mid_phash(rendered),
        }, "ok"
    except (checks.CheckError, ClipSceneError, ValueError, KeyError, OSError) as exc:
        if isinstance(exc, TimeoutError):
            return None, "timeout"
        return None, "processing_failed"


def clip_scene(
    job: dict[str, Any],
    assignment: dict[str, Any],
    out_dir: str | Path,
    *,
    fetch: Any = None,
    scorer: Any = None,
    ocr: Any = None,
    exclude: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """One isolated scene runner: first verified primary/backup wins, or explicit fail."""
    scene_id, _ = _valid_job(job, assignment)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    final_clip = root / "clip.mp4"
    final_clip.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        timeout = float(os.environ.get("CLIPOTECA_SCENE_TIMEOUT", "600"))
    except ValueError as exc:
        raise ClipSceneError("CLIPOTECA_SCENE_TIMEOUT must be numeric") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ClipSceneError("CLIPOTECA_SCENE_TIMEOUT must be positive")
    deadline = started + timeout
    attempts: list[dict[str, str]] = []
    if assignment["status"] == "needs_import":
        report = {
            "version": 1,
            "id": scene_id,
            "status": "fail",
            "reason": "needs_import",
            "tried": [],
        }
        _atomic_json(root / "result.json", report)
        return report
    source = [assignment.get("primary")] if assignment.get("primary") else []
    source += list(assignment.get("backups", []))
    skipped = set(exclude)
    for index, candidate in enumerate(source):
        if not isinstance(candidate, dict) or not isinstance(candidate.get("clip_id"), str):
            raise ClipSceneError("assignment candidate missing clip_id")
        cid = candidate["clip_id"]
        if cid in skipped:
            attempts.append({"clip_id": cid, "reason": "excluded"})
            continue
        if time.monotonic() >= deadline:
            attempts.append({"clip_id": cid, "reason": "timeout"})
            break
        with tempfile.TemporaryDirectory(prefix=".clipscene-", dir=root) as folder:
            tmp = Path(folder)
            downloaded = tmp / "download.mp4"
            rendered = tmp / "render.mp4"
            try:
                _download(candidate, downloaded, fetch)
            except (Exception,) as exc:
                if isinstance(exc, TimeoutError):
                    attempts.append({"clip_id": cid, "reason": "timeout"})
                    break
                attempts.append({"clip_id": cid, "reason": "download_failed"})
                continue
            if scorer is None:
                try:
                    from . import models
                    from .clipscore import ClipScorer

                    scorer = ClipScorer(models.cache_dir() / "clip-vit-b32")
                except (OSError, RuntimeError, ValueError):
                    attempts.append({"clip_id": cid, "reason": "processing_failed"})
                    continue
            try:
                result, reason = _attempt(
                    job, candidate, downloaded, rendered, scorer, ocr, deadline
                )
            except TimeoutError:
                result, reason = None, "timeout"
            if result is not None:
                os.replace(rendered, final_clip)
                report = {
                    "version": 1,
                    "id": scene_id,
                    "status": "ok",
                    "clip": result,
                    "used": "primary" if index == 0 else "backup",
                    "tried": attempts,
                    "time_s": round(time.monotonic() - started, 3),
                }
                _atomic_json(root / "result.json", report)
                return report
            attempts.append({"clip_id": cid, "reason": reason})
            if reason == "timeout":
                break
    report = {
        "version": 1,
        "id": scene_id,
        "status": "fail",
        "reason": "no_candidate",
        "tried": attempts,
    }
    _atomic_json(root / "result.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.clipscene")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        assignment = json.loads(args.assignment.read_text(encoding="utf-8"))
        report = clip_scene(job, assignment, args.out, exclude=args.exclude)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"clipscene: {type(exc).__name__}: invalid input or scene\n")
    return 0 if report["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
