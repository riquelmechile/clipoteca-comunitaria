"""Bounded scene-cut detection. Does not normalize, publish, or modify source videos.

Design informed by broll-mcp (MIT); this is an independent implementation.
Untrusted URLs must pass the existing SSRF validator and downloader.
"""

from __future__ import annotations

import asyncio
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

from ..config import USER_AGENT
from ..security.download import DownloadError, download_media
from ..security.ssrf import UnsafeMediaURL, validate_public_url
from .process_env import safe_media_env

SCENE_FILTER = "scale=320:-2,select='gt(scene,0.3)',showinfo"
CUT_RE = re.compile(r"\bpts_time:\s*(\d+(?:\.\d+)?)")
DURATION_RE = re.compile(r"\bDuration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
MIN_SCENE_SECONDS = 1.5
MAX_SCENE_SECONDS = 20.0
SPLIT_STEP_SECONDS = 10.0
MAX_SCENES = 120
MAX_WINDOW_SECONDS = 900.0


class SceneDetectionError(RuntimeError):
    """No meaningful scene timeline can be obtained."""


@dataclass(frozen=True, slots=True)
class Scene:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _validate_window(start: float, max_window: float) -> None:
    if not (0 <= start < float("inf")):
        raise ValueError("start must be finite and non-negative")
    if not (0 < max_window <= MAX_WINDOW_SECONDS):
        raise ValueError("max_window must be in (0, 900]")


def _duration(stderr: str) -> float | None:
    match = DURATION_RE.search(stderr)
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def parse_cuts(stderr: str, *, start: float = 0) -> list[float]:
    """Extract timestamps selected by showinfo and normalize to source seconds."""
    cuts = []
    for hit in CUT_RE.finditer(stderr):
        value = float(hit.group(1)) + start
        if value >= start and (not cuts or value - cuts[-1] > 0.05):
            cuts.append(value)
    return cuts


def normalize_scenes(
    cuts: list[float] | list[Scene],
    *,
    start: float = 0,
    end: float,
    max_scenes: int = MAX_SCENES,
) -> list[Scene]:
    """Merge sub-1.5s cuts and subdivide long takes into 10s windows."""
    if not 0 <= start < end or end - start > MAX_WINDOW_SECONDS + 1e-6:
        raise ValueError("invalid scene bounds")
    if not 1 <= max_scenes <= MAX_SCENES:
        raise ValueError("max_scenes must be 1..120")
    boundaries = [start, end]
    for item in cuts:
        if isinstance(item, Scene):
            boundaries.extend((item.start, item.end))
        elif isinstance(item, (int, float)):
            boundaries.append(float(item))
        else:
            raise TypeError("scene boundaries must be numeric or Scene")
    # Keep the requested endpoints exact: rounding an end like 3.14159 can
    # silently truncate the final scene by up to 0.00005 seconds.
    rounded = (round(t, 4) for t in boundaries if start < t < end)
    boundaries = sorted({start, end, *(t for t in rounded if start < t < end)})
    segments = [Scene(a, b) for a, b in zip(boundaries, boundaries[1:]) if b > a]
    if not segments:
        return [Scene(start, end)]
    # Merge short segments into their neighbour, never creating zero duration.
    index = 0
    while index < len(segments) and len(segments) > 1:
        item = segments[index]
        if item.duration >= MIN_SCENE_SECONDS:
            index += 1
        elif index == 0:
            segments[1] = Scene(item.start, segments[1].end)
            segments.pop(0)
        else:
            segments[index - 1] = Scene(segments[index - 1].start, item.end)
            segments.pop(index)
            index -= 1
    output = []
    for segment in segments:
        cursor = segment.start
        if segment.duration > MAX_SCENE_SECONDS:
            while cursor < segment.end:
                next_cut = min(cursor + SPLIT_STEP_SECONDS, segment.end)
                if 0 < segment.end - next_cut < MIN_SCENE_SECONDS:
                    # Fold a tiny tail into this piece, not a new <1.5s scene.
                    # The merged piece stays below the 20-second cap.
                    next_cut = segment.end
                output.append(Scene(cursor, next_cut))
                cursor = next_cut
        elif segment.end > cursor:
            output.append(Scene(cursor, segment.end))
    if len(output) > max_scenes:
        # Preserve coverage AND the 20-second limit. Under excessive cut density,
        # reduce cut fidelity rather than merging the tail into an overlong scene.
        duration = end - start
        if math.ceil((duration - 1e-9) / MAX_SCENE_SECONDS) > max_scenes:
            raise ValueError("max_scenes cannot cover the window within 20-second scenes")
        count = min(max_scenes, max(1, math.ceil(duration / SPLIT_STEP_SECONDS)))
        step = duration / count
        output = [
            Scene(start + index * step, end if index == count - 1 else start + (index + 1) * step)
            for index in range(count)
        ]
    return output


def _detect_with_scenedetect(
    source: str | Path, *, start: float, max_window: float
) -> list[Scene] | None:
    """Use the optional detector only within the requested local video window.

    This is an alternative to FFmpeg, not an extra full-file analysis pass.
    """
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector
    except ImportError:
        return None
    try:
        video = open_video(str(source))
        video.downscale_factor = 2
        end = min(start + max_window, video.duration.get_seconds())
        if end <= start:
            raise SceneDetectionError("source has no video in requested window")
        if start:
            video.seek(start)
        manager = SceneManager()
        manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15))
        manager.detect_scenes(video=video, end_time=end)
        cuts = [
            scene_start.get_seconds()
            for scene_start, _scene_end in manager.get_scene_list(start_in_scene=True)
            if start < scene_start.get_seconds() < end
        ]
        return normalize_scenes(cuts, start=start, end=end)
    except (ValueError, RuntimeError, OSError, AttributeError):
        # Missing/unsupported optional backend falls back to bounded FFmpeg.
        return None


def _ffmpeg_command(source: str | Path, *, start: float, window: float) -> list[str]:
    """Only process the already-validated local source; never pass remote headers."""
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-ss",
        str(start),
        "-t",
        str(window),
        "-i",
        str(source),
        "-vf",
        SCENE_FILTER,
        "-an",
        "-f",
        "null",
        "-",
    ]


def _ffmpeg_scenes(
    source: str | Path,
    *,
    start: float,
    max_window: float,
    prefer_scenedetect: bool = False,
    runner: Callable | None = None,
) -> list[Scene]:
    _validate_window(start, max_window)
    if prefer_scenedetect:
        detected = _detect_with_scenedetect(source, start=start, max_window=max_window)
        if detected is not None:
            return detected
    cmd = _ffmpeg_command(source, start=start, window=max_window)
    run = runner or subprocess.run
    try:
        proc = run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env=safe_media_env(),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise SceneDetectionError("ffmpeg detection could not complete") from exc
    if proc.returncode:
        # No upstream stderr is emitted (may contain a signed URL).
        raise SceneDetectionError("ffmpeg scene detection failed")
    source_duration = _duration(proc.stderr)
    end = (
        start + max_window if source_duration is None else min(start + max_window, source_duration)
    )
    if end <= start:
        raise SceneDetectionError("source has no video in requested window")
    return normalize_scenes(parse_cuts(proc.stderr, start=start), start=start, end=end)


def detect_scenes(
    source: str | Path,
    *,
    start: float = 0,
    max_window: float = 900,
    headers: dict[str, str] | None = None,
    prefer_scenedetect: bool = False,
    runner: Callable | None = None,
) -> list[Scene]:
    """Sync entrypoint. Remote calls have an async counterpart for event loops."""
    _validate_window(start, max_window)
    if str(source).startswith(("https://", "http://")):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "detect_scenes() cannot run inside an event loop; await detect_scenes_async()"
            ) from None
        return asyncio.run(
            detect_scenes_async(
                source,
                start=start,
                max_window=max_window,
                headers=headers,
                prefer_scenedetect=prefer_scenedetect,
                runner=runner,
            )
        )
    path = Path(source)
    if not path.is_file():
        raise SceneDetectionError("local source does not exist")
    return _ffmpeg_scenes(
        path,
        start=start,
        max_window=max_window,
        prefer_scenedetect=prefer_scenedetect,
        runner=runner,
    )


async def _ffmpeg_scenes_async(
    source: Path, *, start: float, max_window: float,
    prefer_scenedetect: bool, runner: Callable | None,
) -> list[Scene]:
    if runner is not None:
        # Explicit test/integration override; no real subprocess owned by this function.
        return await asyncio.to_thread(
            _ffmpeg_scenes, source, start=start, max_window=max_window,
            prefer_scenedetect=prefer_scenedetect, runner=runner,
        )
    if prefer_scenedetect:
        detected = await asyncio.to_thread(
            _detect_with_scenedetect, source, start=start, max_window=max_window
        )
        if detected is not None:
            return detected
    command = _ffmpeg_command(source, start=start, window=max_window)
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=safe_media_env()
        )
    except OSError as exc:
        raise SceneDetectionError("ffmpeg detection could not complete") from exc
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except BaseException as exc:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.communicate()
        if isinstance(exc, TimeoutError):
            raise SceneDetectionError("ffmpeg detection could not complete") from exc
        raise
    if proc.returncode:
        raise SceneDetectionError("ffmpeg scene detection failed")
    log = stderr.decode(errors="replace")
    duration = _duration(log)
    end = start + max_window if duration is None else min(start + max_window, duration)
    if end <= start:
        raise SceneDetectionError("source has no video in requested window")
    return normalize_scenes(parse_cuts(log, start=start), start=start, end=end)


async def detect_scenes_async(
    source: str | Path,
    *,
    start: float = 0,
    max_window: float = 900,
    headers: dict[str, str] | None = None,
    prefer_scenedetect: bool = False,
    client: httpx.AsyncClient | None = None,
    resolver=None,
    runner: Callable | None = None,
) -> list[Scene]:
    """Detect scenes without blocking an event loop.

    Remote URLs always use the bounded, peer-validated media downloader.
    Unpinned FFmpeg HTTP access and speculative Range probes are disabled.
    Custom remote headers are rejected explicitly, not silently ignored.
    """
    _validate_window(start, max_window)
    if headers:
        raise ValueError("custom headers are unsupported by the secure scene downloader")
    string = str(source)
    if not string.startswith(("http://", "https://")):
        path = Path(source)
        if not path.is_file():
            raise SceneDetectionError("local source does not exist")
        return await _ffmpeg_scenes_async(
            path,
            start=start,
            max_window=max_window,
            prefer_scenedetect=prefer_scenedetect,
            runner=runner,
        )
    if urlsplit(string).scheme != "https":
        raise UnsafeMediaURL("remote scene source must be HTTPS")
    await validate_public_url(string, resolver=resolver)
    owned = client is None
    connection = client or httpx.AsyncClient(
        timeout=httpx.Timeout(40, connect=10, read=25),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with tempfile.TemporaryDirectory(prefix="clipoteca-scene-") as tmp:
            try:
                local = await download_media(
                    string,
                    Path(tmp),
                    client=connection,
                    resolver=resolver,
                )
            except (DownloadError, UnsafeMediaURL) as exc:
                raise SceneDetectionError("remote media unavailable") from exc
            return await _ffmpeg_scenes_async(
                local,
                start=start,
                max_window=max_window,
                prefer_scenedetect=prefer_scenedetect,
                runner=runner,
            )
    finally:
        if owned:
            await connection.aclose()
