"""M29 voice placement, real ffmpeg ducking and two-pass loudness regression tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio.mix import (
    MixError,
    _loudnorm_json,
    build_voice_stem,
    main,
    measure_loudness,
    mix,
    place_voice_samples,
)

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
FIXTURE = Path(__file__).parent / "fixtures/m29/timeline.json"


def _timeline() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _tone(path: Path, hz: int, seconds: float, rate: int = 44100, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={hz}:sample_rate={rate}:duration={seconds}",
            "-ac",
            str(channels),
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )


def _audio(path: Path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as stream:
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2")
        return (
            samples.reshape(-1, stream.getnchannels()).astype(np.float32) / 32767,
            stream.getframerate(),
            stream.getnchannels(),
        )


def test_place_voice_stem_at_exact_samples_with_normalization_and_clip() -> None:
    timeline = {
        "total": 1.0,
        "scenes": [
            {"id": "a", "dur": 0.2, "vo": 0.2},
            {"id": "b", "dur": 0.6, "vo": 0.7},
            {"id": "silent", "dur": 0, "vo": 0},
        ],
    }
    result = place_voice_samples(
        timeline,
        {"a": np.array([0.5, -0.5], dtype=np.float32), "b": np.ones(7, dtype=np.float32)},
        rate=10,
    )
    assert result.shape == (10,)
    assert np.all(result[:2] == 0)
    assert result[2] == pytest.approx(0.445)
    assert result[3] == pytest.approx(-0.445)
    assert result[7:10] == pytest.approx([0.89] * 3)
    assert np.count_nonzero(result) == 5


def test_place_voice_rejects_silence_nan_and_bad_timeline() -> None:
    timeline = {"total": 1.0, "scenes": [{"id": "a", "dur": 0.3, "vo": 0.1}]}
    with pytest.raises(MixError, match="silent"):
        place_voice_samples(timeline, {})
    with pytest.raises(MixError, match="finite mono"):
        place_voice_samples(timeline, {"a": np.array([float("nan")])})
    timeline["scenes"][0]["id"] = "../unsafe"
    with pytest.raises(MixError, match="unsafe"):
        place_voice_samples(timeline, {"a": np.ones(10)})


def test_loudnorm_parser_selects_last_json_and_rejects_missing() -> None:
    text = (
        'ffmpeg progress {"input_i":"-22","input_tp":"-5"}\n'
        'other message {"input_i":"-15.1","input_tp":"-1.2","input_lra":"3.2"}'
    )
    assert _loudnorm_json(text)["input_i"] == "-15.1"
    with pytest.raises(MixError, match="measurement JSON"):
        _loudnorm_json('{"other":"not loudnorm"}')


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_build_voice_stem_converts_16k_to_pcm16_mono(tmp_path: Path) -> None:
    timeline = {
        "total": 2,
        "scenes": [
            {"id": "hello", "dur": 0.6, "vo": 0.3},
            {"id": "quiet", "dur": 0, "vo": 1.5},
        ],
    }
    _tone(tmp_path / "audio" / "hello.wav", 880, 0.6, rate=16000, channels=2)
    report = build_voice_stem(timeline, tmp_path, tmp_path / "stem.wav")
    assert report == 1
    values, rate, channels = _audio(tmp_path / "stem.wav")
    assert rate == 44100 and channels == 1
    assert len(values) == 2 * rate
    assert np.all(values[: int(0.3 * rate)] == 0)
    assert np.max(np.abs(values)) == pytest.approx(0.89, abs=2 / 32767)
    assert np.all(values[int(1.0 * rate) :] == 0)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_missing_voice_wav_fails_explicitly(tmp_path: Path) -> None:
    with pytest.raises(MixError, match="missing voice WAV for scene intro"):
        build_voice_stem(_timeline(), tmp_path, tmp_path / "unused.wav")


def _band_energy(samples: np.ndarray, rate: int, hz: float, start: float, seconds: float) -> float:
    segment = samples[int(start * rate) : int((start + seconds) * rate)]
    window = np.hanning(len(segment))
    spectrum = np.fft.rfft(segment * window)
    bins = np.fft.rfftfreq(len(segment), 1 / rate)
    mask = np.abs(bins - hz) < 8
    return float(np.sqrt(np.sum(np.abs(spectrum[mask]) ** 2)) / max(1, len(segment)))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_mix_real_loudness_true_peak_ducking_and_cli(tmp_path: Path) -> None:
    timeline = _timeline()
    voices = tmp_path / "voice"
    for scene in timeline["scenes"]:
        _tone(
            voices / "audio" / f"{scene['id']}.wav",
            1200,
            scene["dur"],
            rate=44100,
        )
    music_path = tmp_path / "music.wav"
    _tone(music_path, 220, timeline["total"] + 0.5, channels=2)
    target = tmp_path / "mix.wav"
    info = mix(timeline, voices, music_path, target)
    assert target.is_file()
    assert (tmp_path / "mix.json").is_file()
    assert json.loads((tmp_path / "mix.json").read_text()) == info
    audio, rate, channels = _audio(target)
    assert rate == 48000 and channels == 2
    assert len(audio) / rate == pytest.approx(timeline["total"], abs=0.02)
    assert abs(info["lufs"] + 14) <= 1.0
    assert info["true_peak"] <= -0.9
    assert info["voice_lines"] == 3
    assert info["target_lufs"] == -14.0
    assert measure_loudness(target)["lufs"] == pytest.approx(info["lufs"], abs=0.05)
    mono = audio.mean(axis=1)
    music_under_voice = _band_energy(mono, rate, 220, 0.6, 0.35)
    music_without_voice = _band_energy(mono, rate, 220, 1.65, 0.35)
    assert music_under_voice < music_without_voice * 0.9
    assert np.max(np.abs(audio)) < 0.95

    # Exercise the public CLI and its single metadata file, no shell or clip audio.
    timeline_path = tmp_path / "timeline.json"
    timeline_path.write_text(json.dumps(timeline))
    assert (
        main(
            [
                "--timeline",
                str(timeline_path),
                "--voice",
                str(voices),
                "--music",
                str(music_path),
                "--out",
                str(tmp_path / "cli.wav"),
            ]
        )
        == 0
    )


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_mix_out_of_range_target_errors_cleanly(tmp_path: Path) -> None:
    with pytest.raises(MixError, match="loudness targets"):
        mix(_timeline(), tmp_path, tmp_path / "missing.wav", tmp_path / "out.wav", lufs=0.0)
