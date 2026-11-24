"""Per-scene local CLIP selection, verified asset fetch, cover transcode and QA."""

from __future__ import annotations

import argparse
import hashlib
import importlib
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
MAX_ANALYZED_SECONDS = 120.0


def _threshold(job: dict[str, Any], key: str, default: float) -> float:
    value = job.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ClipSceneError(f"job.{key} must be a finite number")
    return float(value)


def _initialize_evaluators(scorer: Any, ocr: Any) -> tuple[Any, Any]:
    """Fail before candidate processing if a required evaluator cannot initialize."""
    hint = "set CLIPOTECA_STUDIO_MODELS; install with uv sync --extra studio"
    if scorer is None or ocr is None:
        try:
            importlib.import_module("cv2")
        except Exception:
            raise ClipSceneError(f"cannot initialize cv2; {hint}") from None
    if scorer is None:
        from . import models
        from .clipscore import MODEL_FILES, ClipScorer

        try:
            scorer = ClipScorer(models.cache_dir() / "clip-vit-b32")
            # The real scorer lazily initializes its inference sessions and tokenizer.
            scorer._sess()
        except ImportError as exc:
            package = exc.name if exc.name in {"onnxruntime", "tokenizers", "cv2"} else None
            label = package or "onnxruntime/tokenizers"
            raise ClipSceneError(f"cannot import {label}; {hint}") from None
        except Exception:
            files = ", ".join(MODEL_FILES)
            raise ClipSceneError(f"cannot initialize CLIP; check {files}; {hint}") from None
    if ocr is None:
        try:
            ocr = checks.get_ocr()
        except Exception:
            raise ClipSceneError(f"cannot initialize rapidocr_onnxruntime; {hint}") from None
    return scorer, ocr


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
    for key, default in (("min_sim", 0.22), ("min_prob", 0.5), ("min_score", 0.0)):
        _threshold(job, key, default)
    status = assignment.get("status")
    if status not in {"catalog", "needs_import", "imported"}:
        raise ClipSceneError("assignment.status must be catalog, imported or needs_import")
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


def _local_source(candidate: dict[str, Any], root: Path | None, destination: Path) -> None:
    """Reject absolute, traversal, symlink and out-of-root paths before verifying bytes."""
    value = candidate.get("local_path")
    if root is None or not isinstance(value, str) or not value:
        raise ClipSceneError("bad_local_path")
    if chr(92) in value or chr(0) in value or ":" in value:
        raise ClipSceneError("bad_local_path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ClipSceneError("bad_local_path")
    try:
        base = root
        source = base / relative
        current = base
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ClipSceneError("bad_local_path")
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(base) or not resolved.is_file():
            raise ClipSceneError("bad_local_path")
        if not _verify(resolved, candidate):
            raise ClipSceneError("hash_mismatch")
        # Copy only verified bytes, then verify the copy to close any source race.
        with resolved.open("rb") as input_file, destination.open("wb") as output:
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
                output.write(block)
        if not _verify(destination, candidate):
            raise ClipSceneError("hash_mismatch")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ClipSceneError):
            raise
        raise ClipSceneError("bad_local_path") from exc


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
        raise checks.DecodeError("media process unavailable") from exc
    if process.returncode:
        raise checks.DecodeError("media process failed")
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
    seconds = min(duration, MAX_ANALYZED_SECONDS)
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
        raise checks.DecodeError("preview ffmpeg missing") from exc
    if result.returncode:
        raise checks.DecodeError("preview ffmpeg failed")
    frame_bytes = width * height * 3
    if not result.stdout or len(result.stdout) % frame_bytes:
        raise checks.DecodeError("preview frame stream incomplete")
    count = len(result.stdout) // frame_bytes
    frames = [
        frame.copy()
        for frame in np.frombuffer(result.stdout, dtype=np.uint8).reshape(count, height, width, 3)
    ]
    return frames, np.arange(count, dtype=np.float32) / 2


def _cuts(path: Path, deadline: float, duration: float) -> list[float]:
    process = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-t",
            str(duration),
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
    deadline: float,
) -> bool:
    import cv2

    values = [min(0.99, max(0.0, (start + ratio * length) / duration)) for ratio in fractions]
    frames = checks.sample_frames(path, values, width=720, deadline=deadline)
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
        sw, sh, duration = checks.probe_video(downloaded, deadline=deadline)
        width, height = framing.target_size(job["aspect"])
        if not framing.resolution_ok(
            sw,
            sh,
            width,
            height,
            int(job.get("min_short_side", 720)),
        ):
            return None, "low_resolution"
        analyzed_duration = min(duration, MAX_ANALYZED_SECONDS)
        mode = fit_mode(analyzed_duration, length)
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
            "dur": analyzed_duration,
            "sharp": sharp,
            "bright": bright,
            "shift": shift,
            "diff": diff,
            "cuts": _cuts(downloaded, deadline, analyzed_duration),
        }
        proposals = windows(analyzed, prompt, length, nemb=negatives)
        chosen, reason = choose_window(proposals, job.get("min_sim", 0.22), job.get("min_prob", 0.5))
        if chosen is None:
            return None, reason
        if chosen["score"] < job.get("min_score", 0.0):
            return None, "low_score"
        begin = chosen["start"]
        actual_span = analyzed_duration if mode == "whole" else length
        if _ocr_positions(downloaded, begin, actual_span, duration, (0.25, 0.75), ocr, deadline):
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
        rw, rh, output_dur = checks.probe_video(rendered, deadline=deadline)
        if rw != width or rh != height:
            return None, "bad_resolution"
        if output_dur < max(2.0, 0.42 * length):
            return None, "short_output"
        black, frozen = checks.black_frozen(rendered, deadline=deadline)
        if black:
            return None, "black"
        if frozen:
            return None, "frozen"
        if _ocr_positions(
            rendered, 0.0, output_dur, output_dur, (0.2, 0.5, 0.8), ocr, deadline
        ):
            return None, "on_screen_text"
        phash = checks.mid_phash(rendered, deadline=deadline)
        if time.monotonic() >= deadline:
            raise TimeoutError("scene timeout")
        return {
            "clip_id": candidate["clip_id"],
            "source_url": candidate.get("source_url"),
            **(
                {"imported": True, "local_path": candidate["local_path"]}
                if "local_path" in candidate
                else {"asset_url": candidate["asset_url"]}
            ),
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
            "phash": phash,
            **({"_analyzed_s": analyzed_duration} if analyzed_duration < duration else {}),
        }, "ok"
    except (TimeoutError, subprocess.TimeoutExpired):
        return None, "timeout"
    except checks.DecodeError:
        return None, "decode_failed"
    except (checks.CheckError, ClipSceneError, ValueError, KeyError, OSError, RuntimeError):
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
    local_root: str | Path | None = None,
) -> dict[str, Any]:
    """One isolated scene runner: first verified primary/backup wins, or explicit fail."""
    scene_id, _ = _valid_job(job, assignment)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    final_clip = root / "clip.mp4"
    final_clip.unlink(missing_ok=True)
    (root / "result.json").unlink(missing_ok=True)
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
    if any(isinstance(item, dict) and item.get("clip_id") not in skipped for item in source):
        scorer, ocr = _initialize_evaluators(scorer, ocr)
    try:
        resolved_root = Path(local_root).resolve() if local_root is not None else None
    except (OSError, RuntimeError):
        resolved_root = None
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
            local = candidate.get("local_path") is not None
            remote = candidate.get("asset_url") is not None
            if local == remote:
                attempts.append({"clip_id": cid, "reason": "invalid_candidate"})
                continue
            try:
                if local:
                    _local_source(
                        candidate,
                        resolved_root,
                        downloaded,
                    )
                else:
                    _download(candidate, downloaded, fetch)
            except (TimeoutError, subprocess.TimeoutExpired):
                attempts.append({"clip_id": cid, "reason": "timeout"})
                if time.monotonic() >= deadline:
                    break
                continue
            except ClipSceneError as exc:
                reason = (
                    str(exc)
                    if local and str(exc) in {"bad_local_path", "hash_mismatch"}
                    else "download_failed"
                )
                attempts.append({"clip_id": cid, "reason": reason})
                continue
            except Exception:
                attempts.append({"clip_id": cid, "reason": "download_failed"})
                continue
            try:
                result, reason = _attempt(
                    job, candidate, downloaded, rendered, scorer, ocr, deadline
                )
            except TimeoutError:
                result, reason = None, "timeout"
            if result is not None:
                analyzed_s = result.pop("_analyzed_s", None)
                os.replace(rendered, final_clip)
                report = {
                    "version": 1,
                    "id": scene_id,
                    "status": "ok",
                    "clip": result,
                    "used": "primary" if index == 0 else "backup",
                    "tried": attempts,
                    "time_s": round(time.monotonic() - started, 3),
                    **({"analyzed_s": analyzed_s} if analyzed_s is not None else {}),
                }
                _atomic_json(root / "result.json", report)
                return report
            attempts.append({"clip_id": cid, "reason": reason})
            if reason == "timeout" and time.monotonic() >= deadline:
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
    parser.add_argument("--local-root", type=Path)
    args = parser.parse_args(argv)
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        assignment = json.loads(args.assignment.read_text(encoding="utf-8"))
        report = clip_scene(
            job,
            assignment,
            args.out,
            exclude=args.exclude,
            local_root=args.local_root,
        )
    except ClipSceneError as exc:
        parser.exit(2, f"clipscene: {exc}\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"clipscene: {type(exc).__name__}: invalid input or scene\n")
    return 0 if report["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
