"""M29 fit tests with real moving clips; no synthetic still-frame padding."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipoteca.studio.fit import FitError, fit_all, fit_clip, fit_plan, main, probe_duration

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
FIXTURE = Path(__file__).parent / "fixtures/m29/timeline.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=90)


def _moving(path: Path, seconds: float) -> None:
    _run(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=30",
        "-t",
        str(seconds),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )


def _stream(path: Path) -> dict:
    output = _run("ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path))
    return json.loads(output.stdout)


@pytest.mark.parametrize(
    ("duration", "target", "mode", "speed"),
    [
        (2.0, 1.0, "trim", 1.0),
        (2.0, 2.0, "trim", 1.0),
        (2.0, 2.5, "slow", 1.25),
        (2.0, 2.50002, "pingpong", 1.2),
        (2.0, 4.8, "pingpong", 4.8 / (2 * 2.0 - 2 / 30)),
    ],
)
def test_fit_plan_exact_borders(
    duration: float,
    target: float,
    mode: str,
    speed: float,
) -> None:
    plan = fit_plan(duration, target)
    assert plan["mode"] == mode
    assert plan["factor"] == pytest.approx(target / duration)
    assert plan["speed"] == pytest.approx(speed)


@pytest.mark.parametrize(
    ("duration", "target", "message"),
    [
        (0, 1, "D"),
        (-1, 1, "D"),
        (1, 0, "Lr"),
        (1, -1, "Lr"),
        (1, float("nan"), "Lr"),
        (1, float("inf"), "Lr"),
        (1, 2.40001, "too_short"),
    ],
)
def test_fit_plan_invalid_or_excessive_ratio(
    duration: float,
    target: float,
    message: str,
) -> None:
    with pytest.raises(FitError, match=message):
        fit_plan(duration, target)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg and ffprobe not installed")
@pytest.mark.parametrize(
    ("target", "mode"),
    [(1.0, "trim"), (2.25, "slow"), (3.8, "pingpong")],
)
def test_fit_clip_real_moving_video(
    tmp_path: Path,
    target: float,
    mode: str,
) -> None:
    source, result = tmp_path / "source.mp4", tmp_path / "fitted.mp4"
    _moving(source, 2.0)
    assert probe_duration(source) == pytest.approx(2.0, abs=1 / 30)
    info = fit_clip(source, result, target)
    assert info["mode"] == mode
    assert info["source_s"] == pytest.approx(2.0, abs=1 / 30)
    assert info["out_s"] >= target - 1 / 30 - 1e-4
    assert info["out_s"] <= target + 0.2 + 1e-4
    streams = _stream(result)["streams"]
    assert len(streams) == 1
    assert streams[0]["codec_type"] == "video"
    assert streams[0]["pix_fmt"] == "yuv420p"
    assert streams[0]["r_frame_rate"] == "30/1"
    assert streams[0]["codec_name"] == "h264"
    detection = _run(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(result),
        "-vf",
        # 0.1 s = three identical frames: catches a held frame at the ping-pong turn,
        # while the two-frame repeats of plain slow motion stay below it.
        "freezedetect=n=0.001:d=0.1",
        "-f",
        "null",
        "-",
    )
    assert "freeze_start" not in detection.stderr


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg and ffprobe not installed")
def test_fit_all_reports_failures_but_keeps_good_scenes(tmp_path: Path) -> None:
    clips = tmp_path / "clips"
    clips.mkdir()
    _moving(clips / "intro.mp4", 2.0)
    _moving(clips / "fact.mp4", 0.5)
    timeline = {
        "total": 9.0,
        "scenes": [
            {"id": "intro", "len": 1.0},
            {"id": "fact", "len": 3.0},
            {"id": "outro", "len": 2.0},
            {"id": "../unsafe", "len": 1.0},
        ],
    }
    output = tmp_path / "fitted"
    report = fit_all(timeline, clips, output)
    assert set(report["scenes"]) == {"intro"}
    assert "too_short" in report["failed"]["fact"]
    assert "missing" in report["failed"]["outro"]
    assert "unsafe" in report["failed"]["../unsafe"]
    assert (output / "intro.mp4").is_file()
    assert json.loads((output / "fit.json").read_text()) == report
    target = tmp_path / "timeline.json"
    target.write_text(json.dumps(timeline))
    assert (
        main(
            [
                "--timeline",
                str(target),
                "--clips",
                str(clips),
                "--out",
                str(output),
            ]
        )
        == 3
    )
    assert not (tmp_path / "unsafe.mp4").exists()


def test_fit_all_bad_timeline_rejects_without_side_effect(tmp_path: Path) -> None:
    with pytest.raises(FitError, match="timeline.scenes"):
        fit_all({}, tmp_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_fit_paths_beginning_dash_are_absolute_in_media_process(tmp_path, monkeypatch):
    folder = tmp_path / "-x"
    folder.mkdir()
    source = folder / "moving.mp4"
    _moving(source, 2.0)
    monkeypatch.chdir(tmp_path)
    result = fit_clip(Path("-x/moving.mp4"), Path("-x/out.mp4"), 1.0)
    assert result["mode"] == "trim"
    assert (folder / "out.mp4").is_file()


def test_fit_stderr_sanitized_and_cli_json(tmp_path, monkeypatch, capsys):
    import clipoteca.studio.fit as module

    source = tmp_path / "private-secret.mp4"
    source.write_bytes(b"not an mp4")

    def runner(argv, timeout=180):
        return subprocess.CompletedProcess(argv, 1, "", f"file {source} invalid")

    monkeypatch.setattr(module, "_run", runner)
    with pytest.raises(FitError) as failure:
        probe_duration(source)
    assert str(tmp_path) not in str(failure.value)
    assert str(source) not in str(failure.value)
    assert "<temporary>" in str(failure.value) or "<path>" in str(failure.value)

    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({"scenes": []}))
    output = tmp_path / "fitted"
    assert main(["--timeline", str(timeline), "--clips", str(tmp_path), "--out", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == {"scenes": {}, "failed": {}}
    timeline.write_text(json.dumps({}))
    with pytest.raises(SystemExit) as exception:
        main(["--timeline", str(timeline), "--clips", str(tmp_path), "--out", str(output)])
    assert exception.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "error": "FitError",
    }
