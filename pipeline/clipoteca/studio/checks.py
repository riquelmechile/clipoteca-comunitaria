"""M28 frame quality, optional OCR and deterministic cross-scene deduplication."""

from __future__ import annotations

import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np


class CheckError(ValueError):
    """Cannot inspect a candidate or verify its quality."""


class DecodeError(CheckError):
    """FFmpeg or FFprobe could not decode the media."""


class InspectionTimeout(CheckError, TimeoutError):
    """Media inspection exceeded its own or the scene deadline."""


def _run(
    args: list[str], timeout: float = 120, *, deadline: float | None = None
) -> subprocess.CompletedProcess[bytes]:
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
    if timeout <= 0:
        raise InspectionTimeout("media inspection timed out")
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise InspectionTimeout("media inspection timed out") from exc
    except OSError as exc:
        raise DecodeError("media inspection unavailable") from exc
    if result.returncode:
        raise DecodeError("media inspection failed: ffmpeg exit " + str(result.returncode))
    return result


def probe_video(path: str | Path, *, deadline: float | None = None) -> tuple[int, int, float]:
    """Read effective display dimensions and duration of the first video stream."""
    import json

    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,nb_frames,avg_frame_rate:"
            "stream_side_data=rotation:stream_tags=rotate",
            "-of",
            "json",
            str(path),
        ],
        deadline=deadline,
    )
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        rotation = next(
            (item["rotation"] for item in stream.get("side_data_list", []) if "rotation" in item),
            stream.get("tags", {}).get("rotate", 0),
        )
        angle = float(rotation) % 360
        if math.isclose(angle, 90, abs_tol=0.1) or math.isclose(angle, 270, abs_tol=0.1):
            width, height = height, width
        duration = float(stream.get("duration", "nan"))
        if not math.isfinite(duration) or duration <= 0:
            numerator, denominator = map(int, stream["avg_frame_rate"].split("/"))
            duration = int(stream["nb_frames"]) * denominator / numerator
        if min(width, height) <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return width, height, duration
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
        raise DecodeError("ffprobe invalid video stream") from exc


def black_frozen(
    path: str | Path, *, deadline: float | None = None
) -> tuple[list[float], list[float]]:
    """Reject black sections >=0.3 s and frozen stretches >=1.5 s."""
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.3:pix_th=0.08,freezedetect=n=0.002:d=1.5",
            "-an",
            "-f",
            "null",
            "-",
        ],
        deadline=deadline,
    )
    stderr = result.stderr.decode("utf-8", "replace")
    black = [float(x) for x in re.findall(r"black_start:\s*([\d.]+)", stderr)]
    frozen = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", stderr)]
    return black, frozen


def sample_frames(
    path: str | Path,
    fractions: list[float] | tuple[float, ...],
    width: int = 720,
    *,
    deadline: float | None = None,
) -> list[np.ndarray]:
    """Extract BGR frames at fractional positions; no decode models or network."""
    if width < 2 or width > 4096:
        raise CheckError("frame sample width out of range")
    source_w, source_h, duration = probe_video(path, deadline=deadline)
    height = max(2, int(round((source_h * width / source_w) / 2)) * 2)
    frames = []
    for fraction in fractions:
        if not math.isfinite(float(fraction)) or not 0 <= float(fraction) <= 1:
            raise CheckError("frame sample position out of range")
        offset = min(max(0.0, duration - 0.05), float(fraction) * duration)
        result = _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-ss",
                f"{offset:.5f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:{height}",
                "-pix_fmt",
                "bgr24",
                "-f",
                "rawvideo",
                "pipe:1",
            ],
            deadline=deadline,
        )
        size = width * height * 3
        if len(result.stdout) != size:
            raise DecodeError("frame decode returned incomplete data")
        frames.append(np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3).copy())
    return frames


def _ocr_result(result: Any) -> list[tuple[str, float]]:
    """Handle RapidOCR's (detections, elapsed) and injected fake formats."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], (int, float)):
        result = result[0]
    if result is None:
        return []
    hits: list[tuple[str, float]] = []
    for entry in result:
        if not isinstance(entry, (tuple, list)):
            continue
        if len(entry) >= 3 and isinstance(entry[1], str):
            word, confidence = entry[1], entry[2]
        elif len(entry) >= 2 and isinstance(entry[0], str):
            word, confidence = entry[0], entry[1]
        else:
            continue
        try:
            value = float(confidence)
        except (ValueError, TypeError):
            continue
        if value >= 0.75 and sum(char.isalnum() for char in word) >= 3:
            hits.append((word, round(value, 2)))
    return hits


_OCR: Any = None


def get_ocr() -> Any:
    """Initialize the default OCR engine once, before evaluating candidates."""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _OCR = RapidOCR()
    return _OCR


def screen_text(frames: list[np.ndarray], ocr: Any = None) -> list[tuple[str, float]]:
    """Read overlay-like text with injectable OCR, importing RapidOCR only on use."""
    if not frames:
        return []
    if ocr is None:
        ocr = get_ocr()
    found: list[tuple[str, float]] = []
    for frame in frames:
        found.extend(_ocr_result(ocr(frame)))
    return found


def mid_phash(path: str | Path, *, deadline: float | None = None) -> str:
    """Perceptual hash of the central decoded frame."""
    import imagehash
    from PIL import Image

    frame = sample_frames(path, [0.5], width=720, deadline=deadline)[0]
    image = Image.fromarray(frame[:, :, ::-1].copy())
    return str(imagehash.phash(image))


def _clip_info(entry: dict[str, Any]) -> dict[str, Any] | None:
    if entry.get("status") == "ok" and isinstance(entry.get("clip"), dict):
        return entry["clip"]
    return None


def is_explicit_contribution(row: dict[str, Any]) -> bool:
    """Only registry-backed image/video contributions get the user-media policy."""
    clip = _clip_info(row)
    ref = clip.get("media_ref") if clip is not None else None
    return (
        row.get("origin") in ("contribution", "contribution_image")
        and isinstance(ref, str)
        and re.fullmatch(r"(?:u[1-9a-f][a-f0-9]{0,11}|a[0-9]{4}-[0-9]{2}-[a-f0-9]{32})", ref)
        is not None
    )


def check_global(
    results: list[dict[str, Any]] | dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Deduplicate automatic clips; preserve deliberate reuse of explicit contributions."""
    import imagehash

    if isinstance(results, dict):
        rows = [dict(value, id=value.get("id", key)) for key, value in results.items()]
    else:
        rows = list(results)
    rows = [row for row in rows if isinstance(row, dict) and _clip_info(row)]
    rows.sort(
        key=lambda row: (
            row.get("origin")
            not in ("contribution", "catalog_pinned", "contribution_image", "repo_asset"),
            -float(row["clip"].get("score", 0)),
            str(row["id"]),
        )
    )
    kept: list[dict[str, Any]] = []
    losers: dict[str, str] = {}
    for row in rows:
        clip = row["clip"]
        reason = None
        for survivor in kept:
            if is_explicit_contribution(row) and is_explicit_contribution(survivor):
                continue
            other = survivor["clip"]
            if clip.get("clip_id") == other.get("clip_id"):
                try:
                    a, b = float(clip["start"]), float(other["start"])
                    length = max(float(clip["len"]), float(other["len"]))
                    if abs(a - b) < length:
                        reason = "duplicate_clip"
                except (ValueError, KeyError, TypeError):
                    reason = "duplicate_clip"
            try:
                first = imagehash.hex_to_hash(str(clip["phash"]))
                second = imagehash.hex_to_hash(str(other["phash"]))
                if first - second <= 6 and reason is None:
                    reason = "duplicate_phash"
            except (KeyError, TypeError, ValueError):
                pass
            if reason is not None:
                break
        if reason:
            losers[str(row["id"])] = reason
        else:
            kept.append(row)
    return losers
