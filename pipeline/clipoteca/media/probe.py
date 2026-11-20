"""Inspeccion de videos mediante ffprobe sin invocar un shell."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .orientation import classify_orientation


class ProbeError(RuntimeError):
    """El archivo no pudo inspeccionarse de forma confiable."""


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


def _fraction(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return round(float(side["rotation"])) % 360
    value = stream.get("tags", {}).get("rotate", 0)
    try:
        return round(float(value)) % 360
    except (TypeError, ValueError):
        return 0


def parse_probe(payload: dict[str, Any], path: Path) -> MediaProbe:
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("ffprobe did not find a video stream")
    width, height = int(video["width"]), int(video["height"])
    rotation = _rotation(video)
    if rotation in {90, 270}:
        width, height = height, width
    average = _fraction(video.get("avg_frame_rate"))
    nominal = _fraction(video.get("r_frame_rate"))
    fps = average or nominal
    duration = float(
        video.get("duration") or payload.get("format", {}).get("duration") or 0
    )
    format_names = str(payload.get("format", {}).get("format_name", "")).split(",")
    suffix = path.suffix.lstrip(".").lower()
    format_name = (
        "mp4" if suffix == "mp4" and "mp4" in format_names else format_names[0]
    )
    orientation = classify_orientation(width, height)
    return MediaProbe(
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        codec=str(video.get("codec_name", "unknown")),
        container=format_name or path.suffix.lstrip(".").lower(),
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        size_bytes=int(payload.get("format", {}).get("size") or path.stat().st_size),
        rotation=rotation,
        variable_frame_rate=bool(average and nominal and abs(average - nominal) > 0.01),
        aspect_ratio=orientation.aspect_ratio,
        orientation=orientation.orientation,
        reduced_ratio=orientation.reduced_ratio,
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
            command, capture_output=True, text=True, timeout=timeout, check=True
        )
        return parse_probe(json.loads(completed.stdout), source)
    except (
        subprocess.SubprocessError,
        json.JSONDecodeError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        raise ProbeError(f"failed to probe media: {exc}") from exc
