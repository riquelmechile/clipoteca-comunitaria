"""Real FFmpeg VFR, 4K and odd dimensions plus subprocess credential isolation."""

import json
import subprocess
from pathlib import Path

from PIL import Image

from clipoteca.jobs import request_id
from clipoteca.media.normalize import normalize_media
from clipoteca.media.process_env import safe_media_env
from clipoteca.media.probe import probe_media


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        check=True,
        timeout=60,
    )


def test_ffmpeg_actual_4k_is_downscaled_without_distortion(tmp_path):
    original = tmp_path / "4k.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:size=3840x2160:rate=1:duration=1",
        "-c:v",
        "mpeg4",
        str(original),
    )
    result = normalize_media(original, tmp_path / "1080p.mp4", timeout=90)
    assert (result.media.width, result.media.height) == (1920, 1080)
    assert result.media.codec == "h264"


def test_ffmpeg_real_odd_source_is_normalized_even(tmp_path):
    original = tmp_path / "odd.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=641x359:rate=5:duration=0.4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv444p",
        str(original),
    )
    source = probe_media(original)
    assert (source.width, source.height) == (641, 359)
    result = normalize_media(original, tmp_path / "even.mp4")
    assert result.media.width % 2 == result.media.height % 2 == 0
    assert (result.media.width, result.media.height) == (640, 358)


def test_ffmpeg_real_vfr_with_timed_png_frames(tmp_path):
    frames = []
    for i in range(5):
        p = tmp_path / f"frame-{i}.png"
        Image.new("RGB", (160, 90), color=(20 + 20 * i, 30, 90)).save(p)
        frames.append(p)
    listing = tmp_path / "frames.txt"
    intervals = (0.08, 0.32, 0.08, 0.48, 0.12)
    listing.write_text(
        "".join(
            "file '" + p.name + "'" + chr(10) + f"duration {d}" + chr(10)
            for p, d in zip(frames, intervals)
        )
        + "file '"
        + frames[-1].name
        + "'"
        + chr(10),
        encoding="utf-8",
    )
    original = tmp_path / "vfr.mp4"
    ffmpeg(
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        "-fps_mode",
        "vfr",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(original),
    )
    observed = probe_media(original)
    assert observed.variable_frame_rate, (observed.fps, observed.fps_fraction)
    result = normalize_media(original, tmp_path / "cfr.mp4")
    assert not result.media.variable_frame_rate
    assert result.media.duration_seconds <= 20


def test_subprocess_environment_excludes_tokens_and_keys(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "dont-leak")
    monkeypatch.setenv("PEXELS_API_KEY", "dont-leak")
    monkeypatch.setenv("SECRET_ACCESS_KEY", "dont-leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    clean = safe_media_env()
    assert "GITHUB_TOKEN" not in clean
    assert "PEXELS_API_KEY" not in clean
    assert "SECRET_ACCESS_KEY" not in clean
    assert clean["PATH"] == "/usr/bin"


def test_request_id_contract_vectors():
    path = Path(__file__).parent / "fixtures/request_id_vectors.json"
    for item in json.loads(path.read_text(encoding="utf-8")):
        assert request_id(item["query"], item["filters"], item["horizon"]) == item["request_id"]
