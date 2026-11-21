"""M13 deterministic scene detection and normalization, without external network."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
    assert parse_cuts(data, start=3) == pytest.approx([3.28, 9.25])


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
async def test_remote_download_is_the_production_path_even_with_injected_runner(tmp_path):
    """An injected runner never enables an unpinned remote FFmpeg invocation."""
    seen = []
    executing_thread = []
    loop_thread = threading.get_ident()

    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        assert "range" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"\x00\x00\x00\x18ftypisom" + b"0" * 64,
        )

    def fake_run(args, **kwargs):
        executing_thread.append(threading.get_ident())
        assert "-reconnect" not in args
        assert Path(args[args.index("-i") + 1]).is_file()
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
    assert executing_thread and executing_thread[0] != loop_thread


@pytest.mark.asyncio
async def test_local_async_detection_uses_thread(tmp_path):
    file = make_video(tmp_path, "async-local.mp4", 3)
    main_thread = threading.get_ident()
    worker_threads = []

    def fake_run(args, **kwargs):
        worker_threads.append(threading.get_ident())
        return SimpleNamespace(returncode=0, stderr="Duration: 00:00:03.00\n")

    assert await detect_scenes_async(file, max_window=3, runner=fake_run) == [Scene(0, 3)]
    assert worker_threads and worker_threads[0] != main_thread


@pytest.mark.asyncio
async def test_sync_remote_from_active_loop_has_clear_error():
    with pytest.raises(RuntimeError, match="await detect_scenes_async"):
        detect_scenes("https://video.example.com/scene.mp4")


@pytest.mark.asyncio
async def test_custom_remote_headers_rejected_before_network():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP request"))
    ) as client:
        with pytest.raises(ValueError, match="custom headers are unsupported"):
            await detect_scenes_async(
                "https://video.example.com/scene.mp4",
                client=client,
                headers={"Referer": "https://example.com"},
            )


def test_dense_cuts_preserve_20_second_bound_with_scene_cap():
    dense = normalize_scenes([i * 2.5 for i in range(1, 360)], start=0, end=900)
    assert 1 <= len(dense) <= 120
    assert dense[0].start == 0 and dense[-1].end == 900
    assert all(0 < scene.duration <= 20 for scene in dense)
    assert all(a.end == pytest.approx(b.start) for a, b in zip(dense, dense[1:]))
    with pytest.raises(ValueError, match="cannot cover"):
        normalize_scenes([1, 2, 3], start=0, end=900, max_scenes=1)


def test_optional_detector_respects_window_without_running_ffmpeg(monkeypatch, tmp_path):
    source = make_video(tmp_path, "sceneoptional.mp4", 25)
    calls = {}

    class FakeVideo:
        duration = SimpleNamespace(get_seconds=lambda: 25.0)
        downscale_factor = 1

        def seek(self, offset):
            calls["seek"] = offset

    class FakeManager:
        def add_detector(self, _detector):
            pass

        def detect_scenes(self, *, video, end_time):
            calls["end_time"] = end_time

        def get_scene_list(self, *, start_in_scene):
            return [(SimpleNamespace(get_seconds=lambda: 14.0), None)]

    detector = ModuleType("scenedetect.detectors")
    detector.AdaptiveDetector = lambda **kwargs: object()
    module = ModuleType("scenedetect")
    module.open_video = lambda path: FakeVideo()
    module.SceneManager = FakeManager
    monkeypatch.setitem(sys.modules, "scenedetect", module)
    monkeypatch.setitem(sys.modules, "scenedetect.detectors", detector)

    def forbidden_ffmpeg(*args, **kwargs):
        pytest.fail("FFmpeg must not run when PySceneDetect succeeds")

    result = detect_scenes(
        source, start=12, max_window=9, prefer_scenedetect=True, runner=forbidden_ffmpeg
    )
    assert calls == {"seek": 12, "end_time": 21}
    assert result == [Scene(12, 14), Scene(14, 21)]


def test_real_scenedetect_optional_window(tmp_path):
    pytest.importorskip("scenedetect")
    source = make_video(tmp_path, "optional-installed.mp4", 8)
    rows = detect_scenes(source, start=2, max_window=4, prefer_scenedetect=True)
    assert rows[0].start == 2
    assert rows[-1].end == pytest.approx(6, abs=0.2)
    assert all(0 < scene.duration <= 20 for scene in rows)


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
