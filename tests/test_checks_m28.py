"""M28 FFmpeg black/freeze detection, injectable OCR and cross-scene QA."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio.checks import (
    black_frozen,
    check_global,
    mid_phash,
    probe_video,
    sample_frames,
    screen_text,
)

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _lavfi(path: Path, input_filter: str, duration: int = 3) -> None:
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
            input_filter,
            "-t",
            str(duration),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=100,
    )


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_black_freeze_and_moving_source(tmp_path: Path) -> None:
    black = tmp_path / "black.mp4"
    frozen = tmp_path / "static.mp4"
    moving = tmp_path / "motion.mp4"
    _lavfi(black, "color=c=black:size=320x180:rate=30", 2)
    _lavfi(frozen, "color=c=red:size=320x180:rate=30", 3)
    _lavfi(moving, "testsrc2=size=320x180:rate=30", 3)
    black_hits, _ = black_frozen(black)
    _, frozen_hits = black_frozen(frozen)
    clean_black, clean_frozen = black_frozen(moving)
    assert black_hits
    assert frozen_hits
    assert not clean_black and not clean_frozen


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_frame_sampler_and_mid_phash(tmp_path: Path) -> None:
    source = tmp_path / "motion.mp4"
    _lavfi(source, "testsrc2=size=320x180:rate=30")
    frames = sample_frames(source, [0.2, 0.5, 0.8], width=360)
    assert len(frames) == 3
    assert all(frame.shape == (202, 360, 3) for frame in frames)
    assert all(frame.dtype == np.uint8 for frame in frames)
    hashed = mid_phash(source)
    assert len(hashed) == 16 and int(hashed, 16) >= 0


def test_screen_text_threshold_and_injected_ocr() -> None:
    seen = []

    def fake(frame):
        seen.append(frame.shape)
        return (
            [
                ([0, 0], "SALE", 0.90),
                ([0, 0], "NO", 0.99),
                ([0, 0], "PRICE", 0.74),
                ([0, 0], "2026", 0.75),
            ],
            0.001,
        )

    results = screen_text([np.zeros((8, 8, 3), dtype=np.uint8)], ocr=fake)
    assert results == [("SALE", 0.9), ("2026", 0.75)]
    assert seen == [(8, 8, 3)]
    assert screen_text([], ocr=fake) == []


def _result(
    scene: str,
    clip_id: str,
    start: float,
    length: float,
    score: float,
    phash: str,
):
    return {
        "id": scene,
        "status": "ok",
        "clip": {
            "clip_id": clip_id,
            "start": start,
            "len": length,
            "score": score,
            "phash": phash,
        },
    }


def test_global_conflicts_score_and_deterministic_ties() -> None:
    low = _result("low", "clip-a", 1, 4, 2, "f" * 16)
    high = _result("high", "clip-a", 2, 4, 5, "0" * 16)
    assert check_global([low, high]) == {"low": "duplicate_clip"}
    near = _result("near", "clip-b", 8, 3, 1, "0" * 15 + "1")
    assert check_global([high, near]) == {"near": "duplicate_phash"}
    different = _result("different", "clip-a", 10, 2, 2, "f" * 16)
    assert check_global([high, different]) == {}
    a = _result("a", "a", 0, 2, 1, "f" * 16)
    b = _result("b", "b", 0, 2, 1, "f" * 16)
    assert check_global([b, a]) == {"b": "duplicate_phash"}
    assert check_global([a, b]) == {"b": "duplicate_phash"}
    assert check_global([{"id": "skip", "status": "fail"}, a]) == {}


def test_global_accepts_id_keyed_result_map() -> None:
    results = {
        "a": _result("a", "a", 0, 2, 4, "0" * 16),
        "b": _result("b", "b", 0, 2, 3, "0" * 16),
    }
    assert check_global(results) == {"b": "duplicate_phash"}


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_rotated_video_preserves_square(tmp_path):
    from clipoteca.studio.framing import crop_filter

    original = tmp_path / "original.mp4"
    rotated = tmp_path / "rotated.mp4"
    output = tmp_path / "vertical.mp4"
    _lavfi(original, "color=blue:size=1280x720:rate=30,drawbox=540:260:200:200:white:t=fill", 1)
    # Try the current FFmpeg input option, then the older MP4 remux syntax.
    for options, after in (
        (["-display_rotation", "90"], []),
        ([], ["-metadata:s:v:0", "rotate=90"]),
    ):
        process = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                *options,
                "-i",
                str(original),
                "-c",
                "copy",
                *after,
                str(rotated),
            ],
            capture_output=True,
            timeout=30,
        )
        if process.returncode == 0 and probe_video(rotated)[:2] == (720, 1280):
            break
    else:
        pytest.skip("FFmpeg cannot write a 90-degree rotation matrix")
    width, height, _ = probe_video(rotated)
    assert (width, height) == (720, 1280)
    preview = sample_frames(rotated, [0.5], width=360)[0]
    assert preview.shape == (640, 360, 3)
    vf, _ = crop_filter(width, height, "9:16")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(rotated),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    assert probe_video(output)[:2] == (1080, 1920)
    frame = sample_frames(output, [0.5], width=540)[0]
    ys, xs = np.where(np.all(frame > 220, axis=2))
    assert len(xs) > 1000
    assert (xs.max() - xs.min() + 1) == pytest.approx(ys.max() - ys.min() + 1, abs=3)
