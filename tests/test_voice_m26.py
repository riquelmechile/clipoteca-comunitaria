from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio.phon import _n, per, toks
from clipoteca.studio.script import MusicSpec, SceneSpec, VideoScript, VoiceSpec
from clipoteca.studio.textnorm import DEFAULT_LEXICON, apply_lexicon, norm_words, wer
from clipoteca.studio.voice import JITTER, Score, Take, line_cache_key, render_script, trim_silence


def _script() -> VideoScript:
    return VideoScript(
        title="Prueba",
        aspect="9:16",
        language="es",
        music=MusicSpec(mood="upbeat", bpm=120),
        voice=VoiceSpec(voice="F1", speed=1.2, lexicon={}),
        scenes=(
            SceneSpec(
                id="fact-1",
                type="fact",
                title="T",
                sub="S",
                say="Motor V6",
                caption="Motor V6",
                query={"es": ["motor"], "en": ["engine"]},
                prompt="motor",
                avoid=(),
            ),
        ),
    )


class FakeEngine:
    model_sha = "a" * 64

    def __init__(self):
        self.takes: list[Take] = []

    def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
        self.takes.append(take)
        audio = np.r_[np.zeros(1600), np.full(3200, 0.1), np.zeros(1600)].astype(
            np.float32,
        )
        return audio, 16000


class FakeScorer:
    per_enabled = True
    reason = "available"

    def __init__(self, values: list[float]):
        self.values = iter(values)

    def score(self, spoken: str, caption: str, samples: np.ndarray, sample_rate: int) -> Score:
        return Score(per=next(self.values))


class WerScorer:
    per_enabled = False
    reason = "per_model_unpinned"

    def score(self, spoken: str, caption: str, samples: np.ndarray, sample_rate: int) -> Score:
        return Score(wer=0.1, asr=caption)


def test_lexicon_longest_and_token_bounds() -> None:
    assert apply_lexicon("RS3 V10 xV6", DEFAULT_LEXICON) == "erre ese tres ve diez xV6"
    assert apply_lexicon("V6", {}) == "V6"


def test_norm_words_and_wer() -> None:
    assert norm_words("Árbol, rápido") == ["arbol", "rapido"]
    assert wer(["uno", "dos"], ["uno", "tres"]) == 0.5
    assert wer([], ["algo"]) == 0.0


def test_norm_words_numbers_when_num2words_available() -> None:
    pytest.importorskip("num2words")
    words = norm_words("1.200 km y 12%")
    assert "mil" in words and "por" in words and "ciento" in words


def test_per_fixtures() -> None:
    path = Path(__file__).parent / "fixtures/m26/per_cases.json"
    for case in json.loads(path.read_text(encoding="utf-8")):
        assert per(case["ref"], case["hyp"]) == pytest.approx(case["expected"])
    assert toks("ˈaː") == ["aː"]
    assert _n("β") == "b"


def test_take_loop_keeps_best_and_respects_jitter(tmp_path: Path) -> None:
    engine = FakeEngine()
    result = render_script(_script(), tmp_path, engine, FakeScorer([0.4, 0.3, 0.2]))
    line = result["lines"]["fact-1"]
    assert line["status"] == "below_threshold"
    assert line["best_take"] == 3
    assert [take.speed for take in engine.takes] == pytest.approx([1.2 * x for x in JITTER[:3]])
    assert (tmp_path / "audio/fact-1.wav").exists()


def test_wer_fallback_and_cache(tmp_path: Path) -> None:
    first = FakeEngine()
    report = render_script(_script(), tmp_path, first, WerScorer())
    assert report["per_check"] == {
        "enabled": False,
        "threshold": 0.12,
        "reason": "per_model_unpinned",
    }
    assert report["lines"]["fact-1"]["status"] == "ok"
    second = FakeEngine()
    render_script(_script(), tmp_path, second, WerScorer())
    assert not second.takes
    saved = json.loads((tmp_path / "voice.json").read_text(encoding="utf-8"))
    assert set(saved) >= {"engine", "model", "lines", "durations", "total_voice_s", "rtf"}


def test_trim_silence_and_cache_key() -> None:
    audio = np.r_[np.zeros(3200), np.ones(1600) * 0.1, np.zeros(3200)].astype(np.float32)
    trimmed = trim_silence(audio, 16000)
    assert 1600 + 1600 + 1280 <= len(trimmed) <= 1600 + 1600 + 1280 + 320
    assert trimmed[0] == 0
    take = Take(1, "F1", 1.2)
    assert line_cache_key("hola", take, "a" * 64) == line_cache_key(
        "hola",
        take,
        "a" * 64,
    )


def test_optional_real_engine() -> None:
    pytest.importorskip("sherpa_onnx")
    from clipoteca.studio.models import cache_dir

    if not (cache_dir() / "supertonic3").exists():
        pytest.skip("Supertonic model not installed")
    from clipoteca.studio.voice import SupertonicEngine

    samples, sample_rate = SupertonicEngine(cache_dir() / "supertonic3").generate(
        "Hola",
        Take(1, "F1", 1.2),
    )
    assert len(samples) > 0 and sample_rate > 0
