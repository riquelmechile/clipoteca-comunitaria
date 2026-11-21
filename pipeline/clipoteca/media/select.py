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
MIN_WINDOW_QUALITY = 0.12
STATIC_MOTION_EPSILON = 0.08


class SceneSelectionError(RuntimeError):
    """Source scene scoring could not complete safely."""


class SceneSelectionTimeout(SceneSelectionError):
    """Bounded scene sampling exceeded its deadline."""


class SceneQualityRejected(SceneSelectionError):
    """The source contains no publishable scene window."""


@dataclass(frozen=True, slots=True)
class SourceWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _scores(frames: bytes) -> list[float]:
    if len(frames) % FRAME_SIZE:
        raise SceneSelectionError("FFmpeg returned truncated scene samples")
    count = len(frames) // FRAME_SIZE
    if not count:
        raise SceneSelectionError("FFmpeg returned no scene samples")

    metrics: list[tuple[float, float, float, int]] = []
    previous: memoryview | None = None
    for index in range(count):
        frame = memoryview(frames)[index * FRAME_SIZE : (index + 1) * FRAME_SIZE]
        luminance = sum(frame) / FRAME_SIZE
        spatial = sum(
            abs(frame[i] - frame[i - 1]) for i in range(1, FRAME_SIZE) if i % FRAME_WIDTH
        ) / (FRAME_SIZE - FRAME_HEIGHT)
        motion = (
            sum(abs(a - b) for a, b in zip(frame, previous)) / FRAME_SIZE
            if previous is not None
            else 0.0
        )
        metrics.append((luminance, spatial, motion, len(set(frame))))
        previous = frame

    nonzero_motion = [motion for _, _, motion, _ in metrics if motion > STATIC_MOTION_EPSILON]
    # Relative normalization keeps gentle rain/camera drift meaningful even when
    # its absolute pixel delta is far below a fast test pattern.
    motion_reference = max(_percentile(nonzero_motion, 0.75), 0.35)
    results: list[float] = []
    for index, (luminance, spatial, motion, diversity) in enumerate(metrics):
        if index == 0 and len(metrics) > 1:
            motion = metrics[1][2]
        if luminance < 30 or luminance > 235 or spatial < 3:
            results.append(0.0)
        elif motion <= STATIC_MOTION_EPSILON:
            # One-frame sub-second media cannot expose motion. Preserve genuinely
            # textured imagery, but reject high-edge title cards with few tones.
            if len(metrics) == 1:
                results.append(0.15 if diversity >= 24 else 0.0)
            else:
                results.append(0.03)
        else:
            texture = min(1.0, spatial / 26)
            relative_motion = min(1.0, motion / motion_reference)
            results.append(texture * 0.4 + relative_motion * 0.6)
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
        f"scale=160:-2:flags=fast_bilinear,fps=1,scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray",
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
    except subprocess.TimeoutExpired as exc:
        raise SceneSelectionTimeout("bounded FFmpeg scene sampling timed out") from exc
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
    """Return publishable non-overlapping windows, best quality then earliest."""
    if not 0 < max_duration <= 20:
        raise ValueError("max_duration must be in (0, 20]")
    if not 1 <= max_clips <= 3:
        raise ValueError("max_clips must be 1..3")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    path = Path(source)
    total = probe_media(path, timeout=min(timeout, 30)).duration_seconds
    horizon = min(total, MAX_WINDOW_SECONDS)
    scores = _sample_scores(path, horizon, timeout=timeout)
    starts = {0.0}
    if total > max_duration:
        scenes = detect_scenes(path, start=0, max_window=horizon)
        starts.update(scene.start for scene in scenes if scene.duration >= MIN_SCENE_SECONDS)
        # Additional 2s candidates permit avoiding title cards or fades when M13
        # returns one long take, while preserving a finite CPU scoring budget.
        starts.update(float(t) for t in range(0, min(math.ceil(horizon), 900), 2))

    ranked: list[tuple[float, SourceWindow]] = []
    minimum_duration = min(8.0, max_duration, horizon)
    for start in sorted(starts):
        end = min(start + max_duration, horizon)
        if end - start + 1e-6 < minimum_duration:
            continue
        if end - start < MIN_SCENE_SECONDS and total > max_duration:
            continue
        a = int(start)
        b = min(len(scores), max(a + 1, math.ceil(end)))
        selected = scores[a:b]
        if not selected:
            continue
        quality = sum(selected) / len(selected)
        bad_fraction = sum(x <= 0.03 for x in selected) / len(selected)
        # Sub-second sources can contain only one sampled frame, so motion is
        # unknowable; keep textured/non-black media while still rejecting black.
        short_source = horizon < MIN_SCENE_SECONDS
        adjusted = quality if short_source else quality - bad_fraction * 0.35
        quality_floor = 0.02 if short_source else MIN_WINDOW_QUALITY
        if adjusted >= quality_floor:
            ranked.append((adjusted, SourceWindow(start, end)))

    ranked.sort(key=lambda pair: (-pair[0], pair[1].start))
    chosen: list[SourceWindow] = []
    for _quality, window in ranked:
        if all(window.end <= picked.start or window.start >= picked.end for picked in chosen):
            chosen.append(window)
            if len(chosen) == max_clips:
                break
    if not chosen:
        raise SceneQualityRejected("no scene window meets the minimum quality threshold")
    return chosen


def fallback_quality_window(
    source: str | Path,
    *,
    max_duration: float,
    total_duration: float,
    timeout: float = 20,
) -> SourceWindow:
    """Find an early non-black/non-flat window after full ranking times out."""
    horizon = min(total_duration, 60.0)
    scores = _sample_scores(Path(source), horizon, timeout=timeout)
    length = max(1, math.ceil(min(max_duration, total_duration)))
    for start in range(len(scores)):
        end_index = min(len(scores), start + length)
        selected = scores[start:end_index]
        duration = min(max_duration, total_duration - start)
        if duration <= 0:
            break
        if selected[0] <= 0.03:
            continue
        quality = sum(selected) / len(selected)
        bad_fraction = sum(value <= 0.03 for value in selected) / len(selected)
        if quality - bad_fraction * 0.35 >= MIN_WINDOW_QUALITY:
            return SourceWindow(float(start), float(start + duration))
    raise SceneQualityRejected("fallback window is black or below the quality floor")
