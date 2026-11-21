"""Bounded scene-cut detection. Does not normalize, publish, or modify source videos.

Design informed by broll-mcp (MIT); this is an independent implementation.
Untrusted URLs must pass the existing SSRF validator and downloader.
"""

from __future__ import annotations

import asyncio
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
    boundaries = sorted({round(t, 4) for t in boundaries if start <= t <= end})
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
                output.append(Scene(cursor, next_cut))
                cursor = next_cut
        elif segment.end > cursor:
            output.append(Scene(cursor, segment.end))
    if len(output) > max_scenes:
        # Hard cap without violating total scene coverage: merge the remaining
        # tail into one final interval, used by callers as a diagnostic boundary.
        head = output[: max_scenes - 1]
        tail_start = output[max_scenes - 1].start
        output = head + [Scene(tail_start, end)]
    return output


def _detect_with_scenedetect(source: str | Path, *, start: float, end: float) -> list[float] | None:
    """Optional local AdaptiveDetector; no hard dependency or network calls."""
    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector
    except ImportError:
        return None
    try:
        video = open_video(str(source))
        video.downscale_factor = 2
        manager = SceneManager()
        manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15))
        manager.detect_scenes(video=video)
        return [
            a.get_seconds()
            for a, _b in manager.get_scene_list(start_in_scene=True)
            if start < a.get_seconds() < end
        ]
    except (ValueError, RuntimeError, OSError, AttributeError):
        return None


def _ffmpeg_command(
    source: str | Path,
    *,
    start: float,
    window: float,
    remote: bool,
    headers: dict[str, str] | None,
) -> list[str]:
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info"]
    if remote:
        command.extend(
            [
                "-reconnect",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_delay_max",
                "10",
                "-rw_timeout",
                "20000000",
            ]
        )
        if headers:
            allowed = {"referer", "user-agent"}
            for key, value in headers.items():
                if key.lower() not in allowed or "\r" in value or "\n" in value:
                    raise ValueError("only single-line Referer/User-Agent headers are allowed")
            command.extend(["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())])
    command.extend(
        [
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
    )
    return command


def _ffmpeg_scenes(
    source: str | Path,
    *,
    start: float,
    max_window: float,
    remote: bool = False,
    headers: dict[str, str] | None = None,
    prefer_scenedetect: bool = False,
    runner: Callable | None = None,
) -> list[Scene]:
    _validate_window(start, max_window)
    cmd = _ffmpeg_command(source, start=start, window=max_window, remote=remote, headers=headers)
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
    cuts = None
    if prefer_scenedetect and not remote:
        cuts = _detect_with_scenedetect(source, start=start, end=end)
    if cuts is None:
        cuts = parse_cuts(proc.stderr, start=start)
    return normalize_scenes(cuts, start=start, end=end)


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
    """Check remote range support; use bounded downloader on range miss.

    Range probe is read-only, no redirects and only a single requested byte.
    For direct FFmpeg access the target URL has already passed the SSRF
    validator. A production deployment should pin DNS at the FFmpeg boundary.
    """
    _validate_window(start, max_window)
    string = str(source)
    if not string.startswith(("http://", "https://")):
        return detect_scenes(
            Path(source),
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
        timeout=httpx.Timeout(12, connect=5),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        try:
            # Do not buffer the whole body when a server ignores Range and sends 200.
            async with connection.stream(
                "GET",
                string,
                headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT},
                follow_redirects=False,
            ) as probe:
                supports_range = probe.status_code == 206 and probe.headers.get(
                    "content-range", ""
                ).startswith("bytes 0-0/")
        except httpx.HTTPError:
            supports_range = False
        if supports_range and runner is not None:
            # Injection is only for bounded offline tests. Production must use the
            # existing downloader until FFmpeg can pin the validated DNS peer.
            return _ffmpeg_scenes(
                string,
                start=start,
                max_window=max_window,
                remote=True,
                headers=headers,
                runner=runner,
            )
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
            return _ffmpeg_scenes(
                local,
                start=start,
                max_window=max_window,
                prefer_scenedetect=prefer_scenedetect,
                runner=runner,
            )
    finally:
        if owned:
            await connection.aclose()
