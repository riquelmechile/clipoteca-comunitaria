"""Scene-length fit without still-frame padding or external downloads."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class FitError(ValueError):
    """A scene cannot be fitted without fabricating frozen frames."""


_SAFE_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")
_TIMEOUT = 180


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FitError(f"{name}: must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise FitError(f"{name}: must be a positive finite number")
    return number


def _scene_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or ".." in value:
        raise FitError("unsafe scene id")
    return value


def _safe_stderr(value: str) -> str:
    """Remove filesystem paths before reporting a failed media process."""
    result = value.replace(str(Path.cwd().resolve()), "<project>")
    result = result.replace(str(Path(tempfile.gettempdir()).resolve()), "<temporary>")
    return re.sub(r"(?<![A-Za-z0-9])/[^ ]+", "<path>", result)[-200:]


def _run(argv: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise FitError(f"media operation failed: {type(exc).__name__}") from exc


def probe_duration(path: str | Path) -> float:
    """Probe the video stream, not the possibly longer container duration."""
    source = Path(path)
    if not source.is_file():
        raise FitError("missing video clip")
    process = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration,nb_frames,avg_frame_rate",
            "-of",
            "json",
            str(source.resolve()),
        ]
    )
    if process.returncode:
        raise FitError(f"ffprobe failed: {_safe_stderr(process.stderr)}")
    try:
        streams = json.loads(process.stdout)["streams"]
        stream = streams[0]
        seconds = float(stream.get("duration", "nan"))
        if not math.isfinite(seconds) or seconds <= 0:
            frames = int(stream["nb_frames"])
            rate_num, rate_den = map(int, stream["avg_frame_rate"].split("/"))
            seconds = frames * rate_den / rate_num
        return _positive(seconds, "video duration")
    except (KeyError, IndexError, ValueError, TypeError, ZeroDivisionError) as exc:
        raise FitError("video stream has no valid duration") from exc


def fit_plan(duration: float, length: float) -> dict[str, float | str]:
    """Choose trim, <=25% slow motion or <=2.4x ping-pong."""
    duration = _positive(duration, "D")
    length = _positive(length, "Lr")
    factor = length / duration
    if factor <= 1 + 1e-6:
        mode, speed = "trim", 1.0
    elif factor <= 1.25 + 1e-6:
        mode, speed = "slow", min(factor, 1.25)
    elif factor <= 2.4 + 1e-6:
        # The return leg drops its first frame (the pivot is never shown twice), so the
        # loop is two frames short of 2*D: speed up to 1.25x covers that at the 2.4x edge.
        loop = max(2 * duration - 2 / 30, duration)
        mode, speed = "pingpong", min(1.25, max(1.2, length / loop))
    else:
        raise FitError("too_short")
    return {"mode": mode, "factor": factor, "speed": speed}


def _atomic_json(path: Path, value: object) -> None:
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


def fit_clip(src: str | Path, dst: str | Path, length: float) -> dict[str, float | str]:
    """Recode video to 30 fps yuv420p, trimming excess rather than freezing."""
    Lr = _positive(length, "Lr")
    source = Path(src)
    destination = Path(dst)
    duration = probe_duration(source)
    plan = fit_plan(duration, Lr)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".mp4",
        dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        argv = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source.resolve()),
        ]
        if plan["mode"] == "trim":
            argv.extend(["-vf", "fps=30,format=yuv420p"])
        elif plan["mode"] == "slow":
            argv.extend(
                [
                    "-vf",
                    f"setpts={float(plan['speed']):.6f}*(PTS-STARTPTS),fps=30,format=yuv420p",
                ]
            )
        else:
            argv.extend(
                [
                    "-filter_complex",
                    # Forward + backward at the source frame rate, without repeating the
                    # pivot frame, and only then slowed: the turn never holds a frame.
                    "[0:v]setpts=PTS-STARTPTS,split[a][b];"
                    "[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
                    f"[a][r]concat=n=2:v=1:a=0,setpts={float(plan['speed']):.6f}*PTS,"
                    "fps=30,format=yuv420p[o]",
                    "-map",
                    "[o]",
                ]
            )
        argv.extend(
            [
                "-t",
                f"{Lr + 0.1:.6f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "19",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                str(temporary.resolve()),
            ]
        )
        process = _run(argv)
        if process.returncode:
            raise FitError(f"fit ffmpeg failed: {_safe_stderr(process.stderr)}")
        out_s = probe_duration(temporary)
        if out_s < Lr - 1 / 30 - 1e-4 or out_s > Lr + 0.2 + 1e-4:
            raise FitError(f"fit duration mismatch: expected {Lr:.3f}, got {out_s:.3f}")
        os.replace(temporary, destination)
        return {
            "mode": str(plan["mode"]),
            "factor": round(float(plan["factor"]), 6),
            "source_s": round(duration, 3),
            "out_s": round(out_s, 3),
        }
    finally:
        temporary.unlink(missing_ok=True)


def fit_all(
    timeline: dict[str, Any],
    clips_dir: str | Path,
    out_dir: str | Path,
    *,
    scenes_dir: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Write a durable status for every scene, even when one must be re-dispatched."""
    if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
        raise FitError("timeline.scenes must be an array")
    rows: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    seen: set[str] = set()
    root = Path(clips_dir)
    output = Path(out_dir)
    for index, scene in enumerate(timeline["scenes"]):
        try:
            if not isinstance(scene, dict):
                raise FitError("scene must be an object")
            key = _scene_id(scene.get("id"))
            if key in seen:
                raise FitError("duplicate scene id")
            seen.add(key)
            length = _positive(scene.get("len"), "scene.len")
            source = root / f"{key}.mp4"
            if scenes_dir is not None:
                result = json.loads(
                    (Path(scenes_dir) / key / "result.json").read_text(encoding="utf-8")
                )
                if (
                    result.get("origin") in ("contribution", "catalog_pinned")
                    and length > 1.25 * probe_duration(source) + 1e-6
                ):
                    raise FitError(
                        "Aporte demasiado corto para la voz real: no se admite pingpong."
                    )
            rows[key] = fit_clip(source, output / f"{key}.mp4", length)
        except (FitError, OSError) as exc:
            failures[str(scene.get("id", index)) if isinstance(scene, dict) else str(index)] = str(
                exc
            )
    report = {"scenes": rows, "failed": failures}
    _atomic_json(output / "fit.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.fit")
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenes", type=Path)
    args = parser.parse_args(argv)
    try:
        report = fit_all(
            json.loads(args.timeline.read_text(encoding="utf-8")),
            args.clips,
            args.out,
            scenes_dir=args.scenes,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}))
        # The module's own messages are already path-free and say what to fix.
        reason = str(exc) if isinstance(exc, FitError) else "invalid input"
        parser.exit(2, f"fit: {type(exc).__name__}: {reason}\n")
    print(json.dumps(report, ensure_ascii=False))
    return 3 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
