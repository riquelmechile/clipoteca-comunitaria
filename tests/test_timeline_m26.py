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
