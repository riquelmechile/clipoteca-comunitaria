"""Inspect media via ffprobe without invoking a shell."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .orientation import classify_orientation


class ProbeError(RuntimeError):
    """Source cannot be inspected reliably."""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    width: int
    height: int
    fps: float
    duration_seconds: float
    codec: str
    container: str
    has_audio: bool
    size_bytes: int
    rotation: int
    variable_frame_rate: bool
    aspect_ratio: str
    orientation: str
    reduced_ratio: str
    pix_fmt: str = ""
    sample_aspect_ratio: str = "1:1"
    fps_fraction: str = "30/1"
    display_width: int = 0
    display_height: int = 0


def _fraction(value: str | None) -> Fraction:
    if not value or value in {"0/0", "N/A"}:
        return Fraction(0)
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return round(float(side["rotation"])) % 360
    try:
        return round(float(stream.get("tags", {}).get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def parse_probe(payload: dict[str, Any], path: Path) -> MediaProbe:
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("ffprobe did not find a video stream")
    raw_width, raw_height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    width, height = raw_width, raw_height
    sar_raw = str(video.get("sample_aspect_ratio") or "1:1")
    sar = _fraction(sar_raw.replace(":", "/"))
    if sar <= 0:
        sar = Fraction(1)
    display_width = max(1, round(raw_width * sar))
    display_height = raw_height
    if rotation in {90, 270}:
        width, height = height, width
        display_width, display_height = display_height, display_width
    average = _fraction(video.get("avg_frame_rate"))
    nominal = _fraction(video.get("r_frame_rate"))
    chosen = average or nominal or Fraction(30)
    duration = float(video.get("duration") or payload.get("format", {}).get("duration") or 0)
    format_names = str(payload.get("format", {}).get("format_name", "")).split(",")
    suffix = path.suffix.lstrip(".").lower()
    fmt = "mp4" if suffix == "mp4" and "mp4" in format_names else format_names[0]
    orient = classify_orientation(display_width, display_height)
    return MediaProbe(
        width=width,
        height=height,
        fps=float(chosen),
        duration_seconds=duration,
        codec=str(video.get("codec_name", "unknown")),
        container=fmt or suffix,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        size_bytes=int(payload.get("format", {}).get("size") or path.stat().st_size),
        rotation=rotation,
        variable_frame_rate=bool(average and nominal and abs(float(average - nominal)) > 0.01),
        aspect_ratio=orient.aspect_ratio,
        orientation=orient.orientation,
        reduced_ratio=orient.reduced_ratio,
        pix_fmt=str(video.get("pix_fmt", "")),
        sample_aspect_ratio=f"{sar.numerator}:{sar.denominator}",
        fps_fraction=f"{chosen.numerator}/{chosen.denominator}",
        display_width=display_width,
        display_height=display_height,
    )


def probe_media(path: str | Path, *, timeout: float = 30) -> MediaProbe:
    source = Path(path)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return parse_probe(json.loads(completed.stdout), source)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
        raise ProbeError(f"failed to probe media: {exc}") from exc
