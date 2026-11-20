from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "pipeline"))

from clipoteca.media.hashing import perceptual_hash, perceptually_similar, sha256_file
from clipoteca.media.normalize import NormalizeError, normalize_media
from clipoteca.media.orientation import classify_orientation
from clipoteca.media.probe import ProbeError, parse_probe, probe_media
from clipoteca.media.thumbnail import create_thumbnail


def ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


@pytest.mark.parametrize(
    ("width", "height", "ratio", "orientation", "reduced"),
    [
        (1080, 1920, "9:16", "vertical", "9:16"),
        (1920, 1080, "16:9", "horizontal", "16:9"),
        (1080, 1080, "1:1", "square", "1:1"),
        (1440, 1080, "other", "other", "4:3"),
        (2520, 1080, "other", "other", "7:3"),
    ],
)
def test_orientation_table(width, height, ratio, orientation, reduced):
    result = classify_orientation(width, height)
    assert (result.aspect_ratio, result.orientation, result.reduced_ratio) == (
        ratio,
        orientation,
        reduced,
    )


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ("180x320", "9:16"),
        ("320x180", "16:9"),
        ("240x240", "1:1"),
        ("320x240", "other"),
    ],
)
def test_real_ffmpeg_aspect_fixtures(tmp_path: Path, size: str, expected: str):
    source = tmp_path / f"{size}.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:size={size}:rate=5:duration=0.4",
        "-c:v",
        "libx264",
        str(source),
    )
    assert probe_media(source).aspect_ratio == expected


@pytest.mark.parametrize(
    ("codec", "extension"),
    [("mpeg4", "avi"), ("libvpx-vp9", "webm"), ("libx264", "mp4")],
)
def test_probe_and_normalize_codecs(tmp_path: Path, codec: str, extension: str):
    source = tmp_path / f"source.{extension}"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24:duration=1",
        "-c:v",
        codec,
        str(source),
    )
    observed = probe_media(source)
    assert observed.width == 320 and observed.height == 180
    result = normalize_media(source, tmp_path / f"normalized-{extension}.mp4")
    assert result.media.codec == "h264"
    assert result.media.aspect_ratio == "16:9"
    assert result.media.fps == pytest.approx(24, abs=0.1)


def test_normalize_trims_24_seconds_preserves_no_audio(tmp_path: Path):
    source = tmp_path / "long.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:size=160x90:rate=12:duration=24",
        "-c:v",
        "libx264",
        str(source),
    )
    result = normalize_media(source, tmp_path / "trimmed.mp4")
    assert 19.8 <= result.media.duration_seconds <= 20.05
    assert result.media.has_audio is False


def test_rotation_and_vfr_parsing(tmp_path: Path):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "30/1",
                "side_data_list": [{"rotation": -90}],
            }
        ],
        "format": {"format_name": "mov,mp4", "duration": "1.0", "size": "42"},
    }
    placeholder = tmp_path / "x.mp4"
    placeholder.write_bytes(b"x")
    observed = parse_probe(payload, placeholder)
    assert (observed.width, observed.height, observed.rotation) == (1080, 1920, 270)
    assert observed.aspect_ratio == "9:16"
    assert observed.variable_frame_rate is True


def test_thumbnail_and_hashes(tmp_path: Path):
    source = tmp_path / "source.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=10:duration=2",
        "-c:v",
        "libx264",
        str(source),
    )
    thumbnail = create_thumbnail(source, tmp_path / "thumb.webp")
    assert thumbnail.stat().st_size > 0
    assert len(sha256_file(source)) == 64
    fingerprint = perceptual_hash(source, frames=3)
    assert len(fingerprint.split(":")) == 3
    assert perceptually_similar(fingerprint, fingerprint)


def test_corrupt_file_fails_explicitly(tmp_path: Path):
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not a video")
    with pytest.raises(ProbeError):
        probe_media(source)
    with pytest.raises((ProbeError, NormalizeError)):
        normalize_media(source, tmp_path / "out.mp4")
