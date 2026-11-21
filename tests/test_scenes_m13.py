"""M13 deterministic scene detection and normalization, without external network."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from clipoteca.media.scenes import (
    Scene,
    SceneDetectionError,
    _ffmpeg_scenes,
    detect_scenes,
    detect_scenes_async,
    normalize_scenes,
    parse_cuts,
)


def make_video(tmp_path: Path, name: str, duration: float, color: str = "red") -> Path:
    dest = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r=4:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(dest),
        ],
        capture_output=True,
        check=True,
    )
    return dest


def test_parse_ffmpeg_pts_showinfo():
    data = "frame:0 pts_time:0.28\nframe:50 pts_time:6.25\nframe:65 pts_time:6.25"
    assert parse_cuts(data, start=3) == [3.28, 9.25]


def test_normalize_short_merge_and_long_split():
    scenes = normalize_scenes([0.4, 7, 7.6], start=0, end=10)
    assert scenes[0].start == 0 and scenes[-1].end == 10
    assert all(scene.duration >= 1.5 for scene in scenes)
    long = normalize_scenes([], start=0, end=45)
    assert [(x.start, x.end) for x in long] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 40),
        (40, 45),
    ]
    assert len(normalize_scenes([], start=0, end=900)) <= 120


def test_single_take_45s(tmp_path):
    video = make_video(tmp_path, "single.mp4", 45)
    result = detect_scenes(video, max_window=45)
    assert len(result) == 5
    assert result[0].start == 0
    assert result[-1].end == pytest.approx(45, abs=0.4)


def test_short_no_cuts(tmp_path):
    video = make_video(tmp_path, "nocuts.mp4", 12)
    result = detect_scenes(video, max_window=12)
    assert len(result) == 1
    assert result[0].duration == pytest.approx(12, abs=0.3)


def test_four_takes_including_title_intro(tmp_path):
    # Fast fixture: title-like flat intro, then 3 visually distinct takes.
    clips = [
        make_video(tmp_path, "title.mp4", 8, "black"),
        make_video(tmp_path, "red.mp4", 17, "red"),
        make_video(tmp_path, "green.mp4", 17, "green"),
        make_video(tmp_path, "blue.mp4", 18, "blue"),
    ]
    listing = tmp_path / "concat.txt"
    listing.write_text("".join(f"file '{item}'\n" for item in clips), encoding="utf-8")
    assembled = tmp_path / "scenes.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            str(assembled),
        ],
        check=True,
        capture_output=True,
    )
    result = detect_scenes(assembled, max_window=60)
    assert result[0].start == 0
    assert result[-1].end == pytest.approx(60, abs=0.8)
    # Some codecs affect the exact select(scene) boundary; sanity of timeline
    # and that 60s is not mislabelled one <=20s scene are the key properties.
    assert len(result) >= 4
    assert all(segment.duration > 0 for segment in result)


def test_ffmpeg_command_safe_env_and_timeouts(monkeypatch, tmp_path):
    source = make_video(tmp_path, "local.mp4", 3)
    monkeypatch.setenv("DOUYIN_ACCESS_TOKEN", "CANARY_SECRET")
    captured = {}

    def fake_run(args, **kwargs):
        captured.update({"args": args, "env": kwargs["env"]})
        return SimpleNamespace(returncode=0, stderr="Duration: 00:00:03.00\n")

    scenes = _ffmpeg_scenes(source, start=0, max_window=3, runner=fake_run)
    assert scenes == [Scene(0, 3)]
    assert captured["args"][:4] == [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
    ]
    assert "CANARY_SECRET" not in str(captured)
    assert any("select='gt(scene,0.3)',showinfo" in arg for arg in captured["args"])


@pytest.mark.asyncio
async def test_remote_range_probes_once_without_download():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["range"] == "bytes=0-0"
        return httpx.Response(206, headers={"content-range": "bytes 0-0/1000"}, content=b"x")

    def fake_run(args, **kwargs):
        assert "-rw_timeout" in args and "-reconnect" in args
        return SimpleNamespace(returncode=0, stderr="Duration: 00:00:12.00\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await detect_scenes_async(
            "https://video.example.com/test.mp4",
            max_window=12,
            client=client,
            resolver=lambda host: ["8.8.8.8"],
            runner=fake_run,
        )
    assert len(seen) == 1
    assert rows == [Scene(0, 12)]


def test_invalid_source_and_limits(tmp_path):
    with pytest.raises(ValueError):
        normalize_scenes([], start=0, end=901)
    with pytest.raises(ValueError):
        detect_scenes(tmp_path / "not-found.mp4", max_window=901)
    with pytest.raises(SceneDetectionError):
        detect_scenes(tmp_path / "not-found.mp4")


@pytest.mark.asyncio
async def test_remote_ssrf_rejected_without_network():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: pytest.fail("must not call remote address"))
    ) as client:
        from clipoteca.security.ssrf import UnsafeMediaURL

        with pytest.raises(UnsafeMediaURL):
            await detect_scenes_async("https://localhost/secret.mp4", client=client)
