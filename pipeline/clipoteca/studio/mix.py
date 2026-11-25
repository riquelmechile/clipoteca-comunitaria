"""M29 voice/music mixing with two-pass loudness and true-peak gates."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np


class MixError(ValueError):
    """Invalid stems, unavailable ffmpeg, or a mix outside loudness limits."""


RATE = 44100
TIMEOUT = 180
_VOICE_FX = (
    "highpass=f=80:poles=2,deesser=i=0.22:m=0.45:f=0.55:s=o,"
    "acompressor=threshold=-18dB:ratio=2.4:attack=8:release=90:makeup=2.5:knee=6,"
    "equalizer=f=200:t=q:w=0.9:g=-1.5,"
    "equalizer=f=3500:t=h:w=2200:g=2.8,alimiter=limit=0.95:level=false"
)


def _number(value: object, field: str, *, min_value: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MixError(f"{field}: must be finite and >= {min_value}")
    value = float(value)
    if not math.isfinite(value) or value < min_value:
        raise MixError(f"{field}: must be finite and >= {min_value}")
    return value


def _timeline(timeline: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
        raise MixError("timeline.scenes must be an array")
    total = _number(timeline.get("total"), "timeline.total", min_value=0.001)
    scenes = timeline["scenes"]
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise MixError(f"timeline.scenes[{index}] must be an object")
        _number(scene.get("dur"), f"timeline.scenes[{index}].dur")
        _number(scene.get("vo"), f"timeline.scenes[{index}].vo")
        key = scene.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or ".." in key:
            raise MixError(f"timeline.scenes[{index}].id unsafe")
    return total, scenes


def _safe_stderr(value: str) -> str:
    result = value.replace(str(Path.cwd().resolve()), "<project>")
    result = result.replace(str(Path(tempfile.gettempdir()).resolve()), "<temporary>")
    return re.sub(r"(?<![A-Za-z0-9])/[^ ]+", "<path>", result)[-350:]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise MixError(f"ffmpeg failed: {type(exc).__name__}") from exc
    if process.returncode:
        raise MixError(f"ffmpeg failed: {_safe_stderr(process.stderr)}")
    return process


def _atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_json(path: Path, contents: dict[str, Any]) -> None:
    data = (json.dumps(contents, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    _atomic_bytes(path, data)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            channels = stream.getnchannels()
            width = stream.getsampwidth()
            if width != 2 or channels not in (1, 2) or rate <= 0:
                raise MixError(f"{path.name}: expected PCM16 mono or stereo WAV")
            raw = stream.readframes(stream.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise MixError(f"{path.name}: unreadable WAV") from exc
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels)
    return data.mean(axis=1) / 32767, rate


def _write_mono(path: Path, samples: np.ndarray) -> None:
    import io

    stream = io.BytesIO()
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(data.tobytes())
    _atomic_bytes(path, stream.getvalue())


def place_voice_samples(
    timeline: dict[str, Any],
    lines: dict[str, np.ndarray],
    rate: int = RATE,
) -> np.ndarray:
    """Pure, sample-exact placement with 0.89 peak normalization and clipping."""
    total, scenes = _timeline(timeline)
    if rate <= 0:
        raise MixError("sample rate must be positive")
    count = int(total * rate)
    output = np.zeros(count, dtype=np.float32)
    for scene in scenes:
        if scene["dur"] <= 0 or scene["id"] not in lines:
            continue
        line = np.asarray(lines[scene["id"]], dtype=np.float32)
        if line.ndim != 1 or not np.isfinite(line).all():
            raise MixError(f"{scene['id']}: voice samples must be finite mono")
        start = int(float(scene["vo"]) * rate)
        if start >= count:
            continue
        amount = min(len(line), count - start)
        output[start : start + amount] += line[:amount]
    peak = float(np.max(np.abs(output))) if output.size else 0.0
    if peak <= 1e-6:
        raise MixError("voice stem is silent")
    return output / peak * 0.89


def build_voice_stem(
    timeline: dict[str, Any],
    voice_dir: str | Path,
    out_path: str | Path,
) -> int:
    """Assemble and normalize M26 scene WAVs, resampling with ffmpeg if necessary."""
    _, scenes = _timeline(timeline)
    root = Path(voice_dir)
    lines: dict[str, np.ndarray] = {}
    for scene in scenes:
        if scene["dur"] <= 0 or ("wav" in scene and not scene["wav"]):
            continue
        key = scene["id"]
        path = root / "audio" / f"{key}.wav"
        if not path.is_file():
            raise MixError(f"missing voice WAV for scene {key}")
        data, rate = _read_wav(path)
        if rate != RATE:
            with tempfile.TemporaryDirectory(prefix="voice-resample-") as folder:
                converted = Path(folder) / "resampled.wav"
                _run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-nostdin",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(path.resolve()),
                        "-ar",
                        str(RATE),
                        "-ac",
                        "1",
                        "-c:a",
                        "pcm_s16le",
                        str(converted.resolve()),
                    ]
                )
                data, converted_rate = _read_wav(converted)
                if converted_rate != RATE:
                    raise MixError(f"{key}: resampling failed")
        lines[key] = data
    stem = place_voice_samples(timeline, lines)
    _write_mono(Path(out_path), stem)
    return len(lines)


def _loudnorm_json(stderr: str) -> dict[str, Any]:
    """Parse the final loudnorm JSON block despite ffmpeg progress and warnings."""
    for match in reversed(list(re.finditer(r"\{[^{}]*\}", stderr, re.DOTALL))):
        try:
            parsed = json.loads(match.group())
        except (ValueError, TypeError):
            continue
        if "input_i" in parsed and "input_tp" in parsed:
            return parsed
    raise MixError("ffmpeg loudnorm did not return measurement JSON")


def _measure(path: Path, lufs: float, tp: float) -> dict[str, float]:
    process = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path.resolve()),
            "-af",
            f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    parsed = _loudnorm_json(process.stderr)
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    values: dict[str, float] = {}
    for key in keys:
        try:
            number = float(parsed[key])
        except (ValueError, TypeError, KeyError) as exc:
            raise MixError(f"loudnorm missing or invalid {key}") from exc
        if not math.isfinite(number):
            raise MixError(f"loudnorm invalid {key}")
        values[key] = number
    return values


def measure_loudness(path: str | Path) -> dict[str, float]:
    """Report measured integrated LUFS, true peak and loudness range."""
    data = _measure(Path(path), -14.0, -1.0)
    return {
        "lufs": data["input_i"],
        "true_peak": data["input_tp"],
        "lra": data["input_lra"],
    }


def _fit_mix_wav(path: Path, total: float) -> None:
    """Correct small filter latency without concealing a truncated render."""
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (48000, 2, 2):
                raise MixError("mix.wav must be stereo 48000 Hz PCM16")
            frames = wav.getnframes()
            expected = round(total * 48000)
            if abs(frames - expected) > 24000 or frames == 0:
                raise MixError(f"mix duration invalid: {frames / 48000:.3f} vs {total:.3f}")
            if frames == expected:
                return
            data = wav.readframes(min(frames, expected))
            if len(data) != min(frames, expected) * 4:
                raise MixError("mix.wav truncated")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(data + bytes(max(0, expected - frames) * 4))
    except (OSError, EOFError, wave.Error) as exc:
        raise MixError("mix.wav unreadable") from exc


def mix(
    timeline: dict[str, Any],
    voice_dir: str | Path,
    music_path: str | Path,
    out_path: str | Path,
    music_volume: float = 0.55,
    lufs: float = -14.0,
    true_peak: float = -1.5,
) -> dict[str, float | int]:
    """Sidechain music beneath speech, measure twice and enforce loudness gate."""
    total, _ = _timeline(timeline)
    volume = _number(music_volume, "music_volume")
    if volume > 2:
        raise MixError("music_volume must be <= 2")
    target = _number(lufs, "lufs", min_value=-70)
    peak_limit = _number(true_peak, "true_peak", min_value=-20)
    if target > -5 or peak_limit > 0:
        raise MixError("loudness targets outside supported range")
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = destination.with_name("mix.json")
    # An old report must never describe the result of a new, failed attempt.
    metadata.unlink(missing_ok=True)
    if not Path(music_path).is_file():
        raise MixError("missing music.wav")
    with tempfile.TemporaryDirectory(prefix=".mix-", dir=destination.parent) as tmp:
        folder = Path(tmp)
        voice = folder / "voice.wav"
        premix = folder / "premix.wav"
        final = folder / "final.wav"
        count = build_voice_stem(timeline, voice_dir, voice)
        graph = (
            f"[0:a]atrim=0:{total:.6f},volume={volume},"
            "aformat=channel_layouts=stereo[m];"
            f"[1:a]aformat=channel_layouts=stereo,{_VOICE_FX},asplit=2[v1][v2];"
            "[m][v1]sidechaincompress=threshold=0.05:ratio=3.5:"
            "attack=22:release=420:makeup=1:knee=8[md];"
            "[md][v2]amix=inputs=2:normalize=0:dropout_transition=0[o]"
        )
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(Path(music_path).resolve()),
                "-i",
                str(voice.resolve()),
                "-filter_complex",
                graph,
                "-map",
                "[o]",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                "-t",
                f"{total:.6f}",
                str(premix.resolve()),
            ]
        )
        _fit_mix_wav(premix, total)
        measured = _measure(premix, target, peak_limit)
        normalized = (
            f"loudnorm=I={target}:TP={peak_limit}:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true,"
            f"alimiter=limit={min(0.89, 10 ** (peak_limit / 20)):.6f}:level=false"
        )
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(premix.resolve()),
                "-af",
                normalized,
                "-t",
                f"{total:.6f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(final.resolve()),
            ]
        )
        _fit_mix_wav(final, total)
        stats = measure_loudness(final)
        try:
            with wave.open(str(final), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getframerate() != 48000 or wav.getnchannels() != 2:
                    raise MixError("mix.wav must be stereo 48000 Hz")
                if wav.getsampwidth() != 2:
                    raise MixError("mix.wav must be PCM16")
        except (OSError, EOFError, wave.Error) as exc:
            raise MixError("mix.wav unreadable") from exc
        if abs(duration - total) > 0.02:
            raise MixError(f"mix duration invalid: {duration:.3f} vs {total:.3f}")
        if abs(stats["lufs"] - target) > 1.0 or stats["true_peak"] > peak_limit + 0.1:
            raise MixError(
                f"loudness gate failed: {stats['lufs']:.2f} LUFS, {stats['true_peak']:.2f} dBTP"
            )
        report: dict[str, float | int] = {
            **stats,
            "duration_s": round(duration, 4),
            "music_volume": volume,
            "target_lufs": target,
            "target_tp": peak_limit,
            "voice_lines": count,
        }
        staged_json = folder / "mix.json"
        _atomic_json(staged_json, report)
        previous = folder / "old-mix.wav"
        if destination.is_file():
            shutil.copyfile(destination, previous)
        os.replace(final, destination)
        try:
            os.replace(staged_json, metadata)
        except Exception:
            # Do not expose a fresh WAV with no matching JSON after an interruption.
            try:
                if previous.is_file():
                    os.replace(previous, destination)
                else:
                    destination.unlink(missing_ok=True)
            except OSError:
                destination.unlink(missing_ok=True)
            raise
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.mix")
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--music", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = mix(
            json.loads(args.timeline.read_text(encoding="utf-8")),
            args.voice,
            args.music,
            args.out,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}))
        # The module's own messages are already path-free and say what to fix.
        reason = str(exc) if isinstance(exc, MixError) else "invalid input"
        parser.exit(2, f"mix: {type(exc).__name__}: {reason}\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
