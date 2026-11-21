"""Deterministic CPU-only source window ranking, with bounded FFmpeg sampling."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .probe import probe_media
from .process_env import safe_media_env
from .scenes import MAX_WINDOW_SECONDS, MIN_SCENE_SECONDS, detect_scenes

FRAME_WIDTH = 96
FRAME_HEIGHT = 54
FRAME_SIZE = FRAME_WIDTH * FRAME_HEIGHT
MAX_SAMPLES = 900


class SceneSelectionError(RuntimeError):
    """Source scene scoring could not complete safely."""


@dataclass(frozen=True, slots=True)
class SourceWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _scores(frames: bytes) -> list[float]:
    if len(frames) % FRAME_SIZE:
        raise SceneSelectionError("FFmpeg returned truncated scene samples")
    count = len(frames) // FRAME_SIZE
    if not count:
        raise SceneSelectionError("FFmpeg returned no scene samples")
    results: list[float] = []
    previous: memoryview | None = None
    for index in range(count):
        frame = memoryview(frames)[index * FRAME_SIZE : (index + 1) * FRAME_SIZE]
        luminance = sum(frame) / FRAME_SIZE
        spatial = sum(
            abs(frame[i] - frame[i - 1]) for i in range(1, FRAME_SIZE) if i % FRAME_WIDTH
        ) / (FRAME_SIZE - FRAME_HEIGHT)
        motion = (
            (sum(abs(a - b) for a, b in zip(frame, previous)) / FRAME_SIZE)
            if previous is not None
            else 0.0
        )
        previous = frame
        # Black fades, overexposed frames and flat cards have almost no value.
        if luminance < 30 or luminance > 235 or spatial < 3:
            results.append(0.0)
        elif motion < 2.5:
            # Text/cards can have high edges while still being static.
            results.append(0.03)
        else:
            results.append(min(1.0, spatial / 26) * 0.4 + min(1.0, motion / 22) * 0.6)
    return results


def _sample_scores(source: Path, seconds: float, *, timeout: float) -> list[float]:
    if not 0 < seconds <= MAX_WINDOW_SECONDS:
        raise ValueError("sample window exceeds 900 seconds")
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-i",
        str(source),
        "-t",
        f"{seconds:.3f}",
        "-an",
        "-vf",
        f"fps=1,scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=True,
            timeout=timeout,
            env=safe_media_env(),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise SceneSelectionError("bounded FFmpeg scene sampling failed") from exc
    if len(result.stdout) > MAX_SAMPLES * FRAME_SIZE:
        raise SceneSelectionError("scene sample limit exceeded")
    return _scores(result.stdout)


def rank_scene_windows(
    source: str | Path,
    *,
    max_duration: float = 20,
    max_clips: int = 2,
    timeout: float = 90,
) -> list[SourceWindow]:
    """Best non-overlapping <=20s windows, tied by earliest source timestamp."""
    if not 0 < max_duration <= 20:
        raise ValueError("max_duration must be in (0, 20]")
    if not 1 <= max_clips <= 3:
        raise ValueError("max_clips must be 1..3")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    path = Path(source)
    total = probe_media(path, timeout=min(timeout, 30)).duration_seconds
    if total <= max_duration:
        return [SourceWindow(0, total)]
    horizon = min(total, MAX_WINDOW_SECONDS)
    scenes = detect_scenes(path, start=0, max_window=horizon)
    scores = _sample_scores(path, horizon, timeout=timeout)
    starts = {0.0}
    starts.update(scene.start for scene in scenes if scene.duration >= MIN_SCENE_SECONDS)
    # Additional 2s candidates permit avoiding title cards or fades when M13
    # returns one long take, while preserving a finite CPU scoring budget.
    starts.update(float(t) for t in range(0, min(math.ceil(horizon), 900), 2))
    ranked: list[tuple[float, SourceWindow]] = []
    for start in sorted(starts):
        end = min(start + max_duration, horizon)
        if end - start < MIN_SCENE_SECONDS:
            continue
        a = int(start)
        b = min(len(scores), max(a + 1, math.ceil(end)))
        selected = scores[a:b]
        if not selected:
            continue
        # Mean prioritizes windows without long bad portions; a low quality
        # penalty discourages black/static title-card frames within a window.
        quality = sum(selected) / len(selected)
        bad_fraction = sum(x <= 0.03 for x in selected) / len(selected)
        ranked.append((quality - bad_fraction * 0.35, SourceWindow(start, end)))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].start))
    chosen: list[SourceWindow] = []
    for _quality, window in ranked:
        if all(window.end <= picked.start or window.start >= picked.end for picked in chosen):
            chosen.append(window)
            if len(chosen) == max_clips:
                break
    return chosen or [SourceWindow(0, min(max_duration, horizon))]
