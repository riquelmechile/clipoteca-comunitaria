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


def test_real_rotation_from_mov_display_matrix(tmp_path: Path):
    source = tmp_path / "plain.mp4"
    rotated = tmp_path / "rotated.mov"
    ffmpeg(
        "-f", "lavfi", "-i", "color=size=320x180:rate=5:duration=1", "-c:v", "libx264", str(source)
    )
    try:
        ffmpeg("-display_rotation", "90", "-i", str(source), "-c", "copy", str(rotated))
    except subprocess.CalledProcessError:
        # Some distro builds do not include the input display-rotation option.
        pytest.skip("ffmpeg build lacks -display_rotation support")
    observed = probe_media(rotated)
    if not observed.rotation:
        pytest.skip("ffmpeg container muxer did not preserve display matrix")
    assert observed.rotation in {90, 270}
    assert (observed.width, observed.height) == (180, 320)
    assert observed.aspect_ratio == "9:16"


def test_sample_aspect_ratio_for_anamorphic_source(tmp_path: Path):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "64:45",
                "avg_frame_rate": "25/1",
            }
        ],
        "format": {"format_name": "mp4", "duration": 1, "size": 1024},
    }
    path = tmp_path / "anamorphic.mp4"
    path.touch()
    parsed = parse_probe(payload, path)
    assert parsed.aspect_ratio == "16:9"
    assert (parsed.width, parsed.height) == (720, 576)
    assert (parsed.display_width, parsed.display_height) == (1024, 576)


def test_source_destination_alias_rejected_before_cleanup(tmp_path: Path):
    source = tmp_path / "original.mp4"
    source.write_bytes(b"do-not-delete")
    with pytest.raises(ValueError, match="different"):
        normalize_media(source, source)
    assert source.read_bytes() == b"do-not-delete"
    alias = tmp_path / "alias.mp4"
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="different"):
        normalize_media(source, alias)
    assert source.exists()


def test_normalized_no_upscale_120fps_and_audio(tmp_path: Path):
    source = tmp_path / "fast.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=120:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(source),
    )
    result = normalize_media(source, tmp_path / "result.mp4")
    assert (result.media.width, result.media.height) == (320, 180)
    assert result.media.fps <= 60
    assert result.media.has_audio
    assert result.media.pix_fmt == "yuv420p"
    assert result.media.duration_seconds <= 20


def test_fractional_rate_preserved(tmp_path: Path):
    source = tmp_path / "ntsc.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=30000/1001:duration=1",
        "-c:v",
        "libx264",
        str(source),
    )
    result = normalize_media(source, tmp_path / "converted.mp4")
    assert result.media.fps == pytest.approx(30000 / 1001, abs=0.001)
    assert result.media.fps_fraction == "30000/1001"


def test_uniform_phash_rejected(tmp_path: Path):
    source = tmp_path / "uniform.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:size=320x180:rate=5:duration=1",
        "-c:v",
        "libx264",
        str(source),
    )
    fingerprint = perceptual_hash(source, frames=2)
    assert not perceptually_similar(fingerprint, fingerprint)


def test_h265_if_supported(tmp_path: Path):
    available = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
    ).stdout
    if "libx265" not in available:
        pytest.skip("ffmpeg runner does not support libx265")
    source = tmp_path / "hevc.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=size=160x90:rate=12:duration=0.5",
        "-c:v",
        "libx265",
        "-threads",
        "2",
        "-x265-params",
        "pools=1:log-level=error",
        str(source),
    )
    result = normalize_media(source, tmp_path / "converted.mp4")
    assert result.media.codec == "h264"
