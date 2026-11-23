"""Offline, beat-quantized Tone.js music beds and scene-cut accents."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import tempfile
import time
import wave
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import numpy as np

ASSET_DIR = Path(__file__).resolve().parent / "assets"
HTML_FILE = ASSET_DIR / "music.html"
TONE_FILE = ASSET_DIR / "vendor" / "Tone.js"
MOODS = frozenset({"upbeat", "chill", "epic"})
SAMPLE_RATE = 44100
HIT_LEAD_S = 0.4


class MusicError(ValueError):
    """A malformed input or an unavailable, unsafe offline music render."""


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MusicError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise MusicError(f"{field} must be a finite {'positive ' if positive else ''}number")
    return result


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MusicError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise MusicError(f"{field} must be an array")
    return value


def _field(data: dict[str, Any], name: str, field: str) -> Any:
    if name not in data:
        raise MusicError(f"missing {field}")
    return data[name]


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MusicError(f"missing offline asset {path.name}") from exc


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise MusicError(f"cannot read JSON from {path.name}: {exc}") from exc


def music_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Calculate a finite, deterministic rendering plan from M28 plan.json."""
    data = _object(plan, "plan")
    music = _object(_field(data, "music", "plan.music"), "plan.music")
    mood = _field(music, "mood", "plan.music.mood")
    if not isinstance(mood, str) or mood not in MOODS:
        raise MusicError("plan.music.mood must be upbeat, chill or epic")
    bpm = _number(_field(data, "bpm", "plan.bpm"), "plan.bpm", positive=True)
    music_bpm = _number(_field(music, "bpm", "plan.music.bpm"), "plan.music.bpm")
    _number(_field(data, "beat", "plan.beat"), "plan.beat", positive=True)
    total = _number(_field(data, "total_est", "plan.total_est"), "plan.total_est")
    if total <= 0 or music_bpm <= 0:
        raise MusicError("plan music tempo and duration must be positive")
    scenes = _list(_field(data, "scenes", "plan.scenes"), "plan.scenes")
    if not scenes:
        raise MusicError("plan.scenes must not be empty")
    for i, item in enumerate(scenes):
        scene = _object(item, f"plan.scenes[{i}]")
        _number(
            _field(scene, "start_est", f"plan.scenes[{i}].start_est"),
            f"plan.scenes[{i}].start_est",
        )
    beat = 60 / bpm
    intro_end = scenes[1]["start_est"] if len(scenes) > 1 else 4 * beat
    if intro_end < 0 or intro_end > total:
        raise MusicError("plan.scenes[1].start_est must lie inside total_est")
    loop_len = 16 * beat
    head_s = round(intro_end + 2 * loop_len, 3)
    loop_start = intro_end + loop_len
    bed_s = round(math.ceil((total * 1.25 + 6) / beat) * beat, 3)
    key_parts = [
        mood,
        str(bpm),
        str(round(intro_end, 3)),
        "v3",
        _digest(HTML_FILE),
        _digest(TONE_FILE),
    ]
    key = hashlib.sha256("|".join(key_parts).encode("utf-8")).hexdigest()[:16]
    return {
        "key": key,
        "mood": mood,
        "bpm": bpm,
        "beat": beat,
        "bed_s": bed_s,
        "intro_end_est": round(intro_end, 3),
        "head_s": head_s,
        "loop_start": loop_start,
        "loop_len": loop_len,
        "total_est": total,
    }


def _validated_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            if rate != SAMPLE_RATE or channels != 2 or width != 2:
                raise MusicError(f"{path.name} must be stereo 44100 Hz PCM16 WAV")
            payload = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise MusicError(f"cannot read {path.name}: {exc}") from exc
    return np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(np.float32) / 32767, rate


def _wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    import io

    output = io.BytesIO()
    samples = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def _write_wav(path: Path, audio: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    _atomic_bytes(path, _wav_bytes(audio, rate))


def render_head(mp: dict[str, Any], out_dir: str | Path) -> None:
    """Render once in isolated Chromium; fail if any external request is attempted."""
    from playwright.sync_api import sync_playwright

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    limit_ms = round(
        _number(
            float(os.environ.get("CLIPOTECA_MUSIC_TIMEOUT", "180")),
            "CLIPOTECA_MUSIC_TIMEOUT",
            positive=True,
        )
        * 1000
    )
    blocked: list[str] = []

    def intercept(route: Any) -> None:
        url = route.request.url
        parsed = urlsplit(url)
        local = Path(unquote(parsed.path)).resolve() if parsed.scheme == "file" else None
        if (
            parsed.scheme == "file"
            and local is not None
            and local.is_relative_to(ASSET_DIR.resolve())
        ):
            route.continue_()
        else:
            blocked.append(url)
            route.abort()

    try:
        with sync_playwright() as playwright:
            launch: dict[str, Any] = {
                "headless": True,
                "args": [
                    "--autoplay-policy=no-user-gesture-required",
                    "--allow-file-access-from-files",
                ],
                "timeout": limit_ms,
            }
            if os.environ.get("CLIPOTECA_CHROME"):
                launch["executable_path"] = os.environ["CLIPOTECA_CHROME"]
            browser = playwright.chromium.launch(**launch)
            try:
                page = browser.new_page()
                page.set_default_timeout(limit_ms)
                page.route("**/*", intercept)
                page.goto(HTML_FILE.resolve().as_uri(), wait_until="load", timeout=limit_ms)
                timeline = {
                    "bpm": mp["bpm"],
                    "beat": mp["beat"],
                    "total": mp["head_s"],
                    "cuts": [],
                    "scenes": [
                        {"start": 0},
                        {"start": mp["intro_end_est"]},
                        {"start": mp["head_s"] + 60},
                    ],
                }
                bed_b64 = page.evaluate(
                    "([T,M])=>render(T,M)",
                    [
                        timeline,
                        {"mood": mp["mood"], "transpose": 0},
                    ],
                )
                hit_b64 = page.evaluate("(M)=>renderHit(M)", {"mood": mp["mood"], "transpose": 0})
                if blocked:
                    raise MusicError(f"offline render blocked {len(blocked)} external request(s)")
                if not isinstance(bed_b64, str) or not isinstance(hit_b64, str):
                    raise MusicError("offline composer returned no WAV")
                _atomic_bytes(output / "head.wav", base64.b64decode(bed_b64, validate=True))
                _atomic_bytes(output / "hit.wav", base64.b64decode(hit_b64, validate=True))
            finally:
                browser.close()
    except MusicError:
        raise
    except Exception as exc:
        raise MusicError(f"offline music rendering failed: {type(exc).__name__}: {exc}") from exc


def write_bed(out_dir: str | Path, loop_start: float, loop_len: float, length: float) -> Path:
    """Extend the second stabilized 16-beat phrase to an exact sample count."""
    folder = Path(out_dir)
    start = _number(loop_start, "loop_start")
    period = _number(loop_len, "loop_len", positive=True)
    target = _number(length, "length", positive=True)
    if start < 0:
        raise MusicError("loop_start cannot be negative")
    head, rate = _validated_audio(folder / "head.wav")
    a, n, count = round(start * rate), round(period * rate), int(target * rate)
    if n <= 0 or len(head) < a + n:
        raise MusicError("head.wav does not contain the complete stabilized loop")
    if count <= a:
        bed = head[:count]
    else:
        loop = head[a : a + n]
        repeats = math.ceil((count - a) / n)
        bed = np.concatenate([head[:a], np.tile(loop, (repeats, 1))])[:count]
    destination = folder / "bed.wav"
    _write_wav(destination, bed, rate)
    return destination


def render_music(plan: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Build the head and loop only when the exact asset/arrangement key changes."""
    mp = music_plan(plan)
    folder = Path(out_dir)
    metadata = folder / "music.json"
    paths = [folder / name for name in ("head.wav", "bed.wav", "hit.wav")]
    if metadata.is_file() and all(path.is_file() for path in paths):
        try:
            cached = _read_json(metadata)
            if cached.get("key") == mp["key"]:
                return cached
        except MusicError:
            pass
    started = time.monotonic()
    render_head(mp, folder)
    write_bed(folder, mp["loop_start"], mp["loop_len"], mp["bed_s"])
    record = {**mp, "hit_lead_s": HIT_LEAD_S, "time_s": round(time.monotonic() - started, 3)}
    _atomic_json(metadata, record)
    return record


def _validate_timeline(timeline: dict[str, Any]) -> tuple[float, list[float]]:
    source = _object(timeline, "timeline")
    _number(_field(source, "bpm", "timeline.bpm"), "timeline.bpm", positive=True)
    _number(_field(source, "beat", "timeline.beat"), "timeline.beat", positive=True)
    total = _number(_field(source, "total", "timeline.total"), "timeline.total", positive=True)
    scenes = _list(_field(source, "scenes", "timeline.scenes"), "timeline.scenes")
    if not scenes:
        raise MusicError("timeline.scenes must not be empty")
    for i, value in enumerate(scenes):
        scene = _object(value, f"timeline.scenes[{i}]")
        _number(
            _field(scene, "start", f"timeline.scenes[{i}].start"), f"timeline.scenes[{i}].start"
        )
    cuts = _list(_field(source, "cuts", "timeline.cuts"), "timeline.cuts")
    positions = [_number(value, f"timeline.cuts[{index}]") for index, value in enumerate(cuts)]
    if any(value < 0 or value > total for value in positions):
        raise MusicError("timeline cuts must lie inside total")
    return total, positions


def build_music_track(
    music_dir: str | Path, timeline: dict[str, Any], out_path: str | Path
) -> Path:
    """Place 0.4-second-leading transition hits and finish at real timeline.total."""
    total, cuts = _validate_timeline(timeline)
    folder = Path(music_dir)
    metadata_path = folder / "music.json"
    metadata = _read_json(metadata_path)
    bed_s = _number(_field(metadata, "bed_s", "music.bed_s"), "music.bed_s")
    loop_start = _number(_field(metadata, "loop_start", "music.loop_start"), "music.loop_start")
    loop_len = _number(_field(metadata, "loop_len", "music.loop_len"), "music.loop_len")
    if bed_s < total + 1:
        write_bed(folder, loop_start, loop_len, total + 2)
        metadata["bed_s"] = total + 2
        _atomic_json(metadata_path, metadata)
    bed, rate = _validated_audio(folder / "bed.wav")
    hit, hit_rate = _validated_audio(folder / "hit.wav")
    if hit_rate != rate:
        raise MusicError("head and hit sample rates differ")
    count = int((total + 0.5) * rate)
    if len(bed) < count:
        raise MusicError("bed.wav is shorter than the requested music track")
    track = bed[:count].copy()
    lead = int(HIT_LEAD_S * rate)
    for cut in cuts:
        begin = int(cut * rate) - lead
        end = min(count, begin + len(hit))
        if not (end > begin >= 0):
            continue
        segment = hit[: end - begin].copy()
        fade_start = lead + int(0.08 * rate)
        fade_end = min(len(segment), fade_start + int(0.15 * rate))
        if fade_start < len(segment):
            if fade_end > fade_start:
                segment[fade_start:fade_end] *= np.linspace(
                    1, 0, fade_end - fade_start, dtype=np.float32
                )[:, None]
            segment[fade_end:] = 0
        track[begin:end] += segment
    fade_in = min(len(track), int(0.02 * rate))
    track[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)[:, None]
    finish = min(count, int(total * rate))
    fade_out = min(finish, int(1.5 * rate))
    if fade_out:
        track[finish - fade_out : finish] *= np.linspace(1, 0, fade_out, dtype=np.float32)[:, None]
    track[finish:] = 0
    track = np.clip(track, -1, 1)
    destination = Path(out_path)
    _write_wav(destination, track, rate)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.music")
    actions = parser.add_subparsers(dest="command", required=True)
    render = actions.add_parser("render")
    render.add_argument("--plan", type=Path, required=True)
    render.add_argument("--out", type=Path, required=True)
    track = actions.add_parser("track")
    track.add_argument("--music", type=Path, required=True)
    track.add_argument("--timeline", type=Path, required=True)
    track.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            render_music(_read_json(args.plan), args.out)
        else:
            build_music_track(args.music, _read_json(args.timeline), args.out)
    except (MusicError, OSError, ValueError) as exc:
        parser.exit(2, f"music: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
