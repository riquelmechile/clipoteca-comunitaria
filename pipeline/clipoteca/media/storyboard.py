"""Bounded evenly spaced 320px WebP preview frames for one normalized clip."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .probe import probe_media
from .process_env import safe_media_env


class StoryboardError(RuntimeError):
    pass


def storyboard_timestamp(duration: float, fps: float, index: int, count: int) -> float:
    """Timestamp used by both generated frames and the public contract."""
    last_decodable = max(0.0, duration - 1.5 / max(fps, 1))
    return min(duration * (index + 0.5) / count, last_decodable)


def create_storyboard(
    source: str | Path,
    target: str | Path,
    *,
    count: int = 6,
    width: int = 320,
    timeout: float = 30,
) -> tuple[Path, ...]:
    if not 1 <= count <= 8:
        raise ValueError("storyboard count must be 1..8")
    if width != 320:
        raise ValueError("storyboard width must be 320")
    source_path, output_dir = Path(source), Path(target)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = probe_media(source_path, timeout=min(timeout, 30))
    duration = metadata.duration_seconds
    if duration <= 0 or duration > 20.1:
        raise StoryboardError("storyboards require a normalized <=20s clip")
    result: list[Path] = []
    try:
        for index in range(count):
            # FFmpeg input seeking drops frames strictly before -ss. For
            # sub-second clips the final nominal timestamp can be after the
            # last encoded frame, especially with FFmpeg >=8. Bound it with
            # a 1.5-frame margin instead of producing an empty WebP.
            moment = storyboard_timestamp(duration, metadata.fps, index, count)
            path = output_dir / f"storyboard-{index:02d}.webp"
            cmd = [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{moment:.4f}",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-an",
                "-vf",
                "scale=320:-2:flags=lanczos,setsar=1",
                "-c:v",
                "libwebp",
                "-quality",
                "76",
                str(path),
            ]
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                timeout=timeout,
                env=safe_media_env(),
            )
            if not path.is_file() or path.stat().st_size == 0:
                raise StoryboardError("missing generated preview")
            result.append(path)
    except (OSError, subprocess.SubprocessError, StoryboardError) as exc:
        for path in output_dir.glob("storyboard-*.webp"):
            path.unlink(missing_ok=True)
        raise StoryboardError("storyboard generation failed") from exc
    return tuple(result)
