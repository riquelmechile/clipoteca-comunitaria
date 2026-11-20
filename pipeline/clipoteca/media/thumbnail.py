"""Miniatura WebP limpia tomada cerca del 10 % del clip."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .probe import probe_media


class ThumbnailError(RuntimeError):
    pass


def create_thumbnail(source: str | Path, destination: str | Path, *, timeout: float = 45) -> Path:
    input_path, output_path = Path(source), Path(destination)
    if output_path.suffix.lower() != ".webp":
        raise ValueError("thumbnail destination must end in .webp")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = max(0.0, probe_media(input_path).duration_seconds * 0.1)
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-c:v",
        "libwebp",
        "-quality",
        "82",
        str(output_path),
    ]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        output_path.unlink(missing_ok=True)
        raise ThumbnailError("failed to generate thumbnail") from exc
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise ThumbnailError("ffmpeg did not create a thumbnail")
    return output_path
