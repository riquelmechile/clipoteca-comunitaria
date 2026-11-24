"""Offline tests for real WAV duration and Whisper word alignment."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from clipoteca.studio.script import MusicSpec, SceneSpec, VideoScript, VoiceSpec
from clipoteca.studio.timeline import build_timeline, word_timings


class FakeTranscriber:
    def __init__(self, heard: list[list[object]]):
        self.heard = heard
        self.calls: list[dict[str, object]] = []

    def transcribe(self, samples: np.ndarray, **kwargs: object):
        self.calls.append(kwargs)
        words = [SimpleNamespace(word=w, start=a, end=b) for w, a, b in self.heard]
        return iter([SimpleNamespace(words=words)]), None


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/m26/timeline_cases.json").read_text()),
    ids=lambda case: case["name"],
)
def test_word_timings_table(case: dict[str, object]) -> None:
    model = FakeTranscriber(case["heard"])
    result, asr, line_wer, timing = word_timings(
        np.zeros(32000, dtype=np.float32),
        case["caption"],
        "es",
        model,
    )
    assert timing == case["timing"]
    assert [row[1:] for row in result] == case["expected"]
    assert all(0 <= a <= b <= 2 for _, a, b in result)
    assert model.calls[0] == {
        "language": "es",
        "word_timestamps": True,
        "beam_size": 5,
        "vad_filter": False,
        "condition_on_previous_text": False,
    }
    assert asr and line_wer >= 0


def test_no_asr_words_proportional_and_monotone() -> None:
    model = FakeTranscriber([])
    result, asr, line_wer, timing = word_timings(
        np.zeros(16000, dtype=np.float32),
        "Hola, mundo.",
        "es",
        model,
    )
    assert timing == "proporcional"
    assert asr == ""
    assert line_wer == pytest.approx(1.0)
    assert result[0][1] == 0
    assert result[-1][2] == 1
    assert all(0 <= a <= b <= 1 for _, a, b in result)


def test_out_of_bounds_whisper_is_clamped_and_monotone() -> None:
    model = FakeTranscriber([["Primero", -3, 0.8], ["segundo", 0.7, 40]])
    result, *_ = word_timings(np.zeros(16000, dtype=np.float32), "Primero segundo", "es", model)
    assert result == [["Primero", 0.0, 0.8], ["segundo", 0.8, 1.0]]


def _script() -> VideoScript:
    scenes = tuple(
        SceneSpec(
            id=f"scene-{i}",
            type=kind,
            title=f"Titulo {i}",
            sub="",
            say="Hola",
            caption="Hola",
            query={"es": ["a"], "en": ["a"]},
            prompt="prompt",
            avoid=(),
        )
        for i, kind in enumerate(("intro", "fact", "outro"), 1)
    )
    return VideoScript(
        title="Prueba",
        aspect="9:16",
        language="es",
        music=MusicSpec(mood="upbeat", bpm=120),
        voice=VoiceSpec(voice="F1", speed=1.2, lexicon={}),
        scenes=scenes,
    )


def _wav(path: Path, duration_s: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.zeros(round(duration_s * 16000), dtype="<i2").tobytes())


def test_build_timeline_beat_quantization_and_atomic_file(tmp_path: Path) -> None:
    script = _script()
    (tmp_path / "voice.json").write_text(
        json.dumps({"lines": {scene.id: {"status": "ok"} for scene in script.scenes}}),
    )
    for scene, duration in zip(script.scenes, (2.0, 5.0, 1.0), strict=True):
        _wav(tmp_path / "audio" / f"{scene.id}.wav", duration)
    timeline = build_timeline(script, tmp_path, FakeTranscriber([["Hola", 0.1, 0.6]]))
    assert timeline["bpm"] == 120
    assert timeline["beat"] == 0.5
    assert [scene["dur"] for scene in timeline["scenes"]] == [2, 5, 1]
    assert [scene["len"] for scene in timeline["scenes"]] == [3, 6, 3.5]
    assert [scene["start"] for scene in timeline["scenes"]] == [0, 3, 9]
    assert [scene["vo"] for scene in timeline["scenes"]] == [0.3, 3.3, 9.3]
    assert timeline["cuts"] == [3, 9]
    assert timeline["total"] == 12.5
    assert json.loads((tmp_path / "timeline.json").read_text()) == timeline


def test_missing_wav_or_voice_line_fails_clearly(tmp_path: Path) -> None:
    script = _script()
    (tmp_path / "voice.json").write_text(json.dumps({"lines": {}}))
    with pytest.raises(ValueError, match="missing scene"):
        build_timeline(script, tmp_path, FakeTranscriber([]))
    (tmp_path / "voice.json").write_text(
        json.dumps({"lines": {scene.id: {"status": "ok"} for scene in script.scenes}}),
    )
    with pytest.raises(FileNotFoundError, match="missing WAV"):
        build_timeline(script, tmp_path, FakeTranscriber([]))


@pytest.mark.parametrize(
    "bpm,duration,expected",
    [
        (100, 3.6, 4.2),
        (120, 2.9, 3.5),
    ],
)
def test_exact_beat_not_rounded_up(tmp_path: Path, bpm, duration, expected):
    script = _script()
    from dataclasses import replace

    script = replace(script, music=MusicSpec(mood="upbeat", bpm=bpm))
    (tmp_path / "voice.json").write_text(
        json.dumps({"lines": {scene.id: {"status": "ok"} for scene in script.scenes}})
    )
    for scene in script.scenes:
        _wav(tmp_path / "audio" / f"{scene.id}.wav", duration)
    report = build_timeline(script, tmp_path, FakeTranscriber([]))
    assert report["scenes"][1]["len"] == pytest.approx(expected)


def test_rejects_voice_report_from_other_script(tmp_path: Path):
    from clipoteca.studio.script import script_id

    script = _script()
    (tmp_path / "voice.json").write_text(
        json.dumps(
            {
                "script_id": "unrelated",
                "lines": {scene.id: {"status": "ok"} for scene in script.scenes},
            }
        )
    )
    with pytest.raises(ValueError, match="script_id"):
        build_timeline(script, tmp_path, FakeTranscriber([]))
    assert not (tmp_path / "timeline.json").exists()
    assert script_id(script) != "unrelated"


def test_cli_writes_once_and_downloads_exact_whisper_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import sys
    from types import SimpleNamespace
    from clipoteca.studio import timeline as module

    script = _script()
    (tmp_path / "script.json").write_text("{}", encoding="utf-8")
    (tmp_path / "voice.json").write_text(
        json.dumps({"lines": {scene.id: {"status": "ok"} for scene in script.scenes}})
    )
    for scene in script.scenes:
        _wav(tmp_path / "audio" / f"{scene.id}.wav", 1.0)
    snapshot_calls = []
    whisper_calls = []

    def snapshot_download(**kwargs):
        snapshot_calls.append(kwargs)
        return "/tmp/verified-whisper-revision"

    def whisper_model(path, **kwargs):
        whisper_calls.append((path, kwargs))
        return FakeTranscriber([["Hola", 0.0, 0.8]])

    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download)
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_model))
    monkeypatch.setattr(module, "parse_video_script", lambda _raw: script)
    written = []
    original = module._atomic_json

    def record_write(path, obj):
        written.append(Path(path))
        original(path, obj)

    monkeypatch.setattr(module, "_atomic_json", record_write)
    destination = tmp_path / "custom-timeline.json"
    assert (
        module.main(
            [
                "--script",
                str(tmp_path / "script.json"),
                "--voice",
                str(tmp_path),
                "--out",
                str(destination),
            ]
        )
        == 0
    )
    assert written == [destination]
    assert destination.is_file()
    assert not (tmp_path / "timeline.json").exists()
    from clipoteca.studio import models

    spec = models.model_spec("whisper")
    assert snapshot_calls == [{"repo_id": spec["repo"], "revision": spec["revision"]}]
    assert whisper_calls == [
        ("/tmp/verified-whisper-revision", {"compute_type": spec["compute_type"]})
    ]


def test_cli_reports_an_unwritable_out_instead_of_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Orchestrator review: `--out` is a file; passing a directory used to crash."""
    import sys
    from types import SimpleNamespace

    from clipoteca.studio import timeline as module

    script = _script()
    (tmp_path / "script.json").write_text("{}", encoding="utf-8")
    (tmp_path / "voice.json").write_text(
        json.dumps({"lines": {scene.id: {"status": "ok"} for scene in script.scenes}})
    )
    for scene in script.scenes:
        _wav(tmp_path / "audio" / f"{scene.id}.wav", 1.0)
    fake_hub = SimpleNamespace(snapshot_download=lambda **_kwargs: "/tmp/pinned")
    fake_whisper = SimpleNamespace(
        WhisperModel=lambda *_a, **_k: FakeTranscriber([["Hola", 0.0, 0.8]])
    )
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_whisper)
    monkeypatch.setattr(module, "parse_video_script", lambda _raw: script)
    folder = tmp_path / "a-directory"
    folder.mkdir()
    argv = ["--script", str(tmp_path / "script.json"), "--voice", str(tmp_path)]
    with pytest.raises(SystemExit) as stop:
        module.main([*argv, "--out", str(folder)])
    assert stop.value.code == 2
    assert "cannot write the timeline" in capsys.readouterr().err
    assert folder.is_dir() and not list(folder.iterdir())
