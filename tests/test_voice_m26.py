from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio.phon import _n, per, toks
from clipoteca.studio import models, voice
from clipoteca.studio.script import MusicSpec, SceneSpec, VideoScript, VoiceSpec
from clipoteca.studio.textnorm import DEFAULT_LEXICON, apply_lexicon, norm_words, spell_numbers, wer
from clipoteca.studio.voice import JITTER, Score, Take, line_cache_key, render_script, trim_silence


def _script() -> VideoScript:
    return VideoScript(
        title="Prueba",
        aspect="9:16",
        language="es",
        music=MusicSpec(mood="upbeat", bpm=120),
        voice=VoiceSpec(voice="F1", speed=1.2, lexicon={"V6": "ve seis"}),
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
    assert (
        apply_lexicon("RS3 V10 xV6", {"RS3": "erre ese tres", "V10": "ve diez"})
        == "erre ese tres ve diez xV6"
    )
    assert apply_lexicon("RS3 V10 xV6", DEFAULT_LEXICON) == "RS3 V10 xV6"
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
        "wer_threshold": 0.2,
        "reason": "per_model_unpinned",
    }
    assert report["lines"]["fact-1"]["status"] == "ok"
    second = FakeEngine()
    render_script(_script(), tmp_path, second, WerScorer())
    assert not second.takes
    saved = json.loads((tmp_path / "voice.json").read_text(encoding="utf-8"))
    assert set(saved) >= {"engine", "model", "lines", "durations", "total_voice_s", "rtf"}


def test_lexicon_replacement_is_literal() -> None:
    value = "\\1 a\\g<0>"
    assert apply_lexicon("ruta A", {"A": value}) == "ruta " + value


def test_render_merges_default_and_script_lexicon(tmp_path: Path) -> None:
    script = _script()
    report = render_script(script, tmp_path / "default", FakeEngine(), FakeScorer([0.05]))
    assert report["lines"]["fact-1"]["spoken"] == "Motor ve seis"
    custom = VideoScript(
        title=script.title,
        aspect=script.aspect,
        language=script.language,
        music=script.music,
        voice=VoiceSpec(voice="F1", speed=1.2, lexicon={"Motor": "Propulsor", "V6": "ve seis"}),
        scenes=script.scenes,
    )
    report = render_script(custom, tmp_path / "custom", FakeEngine(), FakeScorer([0.05]))
    assert report["lines"]["fact-1"]["spoken"] == "Propulsor ve seis"


def test_whisper_scorer_pins_manifest_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeWhisperModel:
        def __init__(self, repo: str, **kwargs: object) -> None:
            calls.append((repo, kwargs))

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.delenv("CLIPOTECA_WHISPER_COMPUTE", raising=False)
    voice.WhisperScorer()
    spec = models.model_spec("whisper")
    assert calls == [
        (spec["repo"], {"compute_type": spec["compute_type"], "revision": spec["revision"]}),
    ]
    assert len(spec["revision"]) == 40


def test_sample_scores_the_spoken_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str]] = []

    class RecordingEngine(FakeEngine):
        def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
            seen.append(("engine", text, take.voice))
            return super().generate(text, take)

    class RecordingScorer:
        per_enabled = True
        reason = "available"

        def score(self, spoken: str, caption: str, samples: np.ndarray, sample_rate: int) -> Score:
            seen.append(("scorer", spoken, caption))
            return Score(per=0.05)

    monkeypatch.setattr(voice, "SupertonicEngine", RecordingEngine)
    monkeypatch.setattr(voice, "_default_scorer", RecordingScorer)
    voice._sample(tmp_path)
    phrases = json.loads(
        (Path(__file__).parent / "fixtures/m26/frases.json").read_text(encoding="utf-8"),
    )
    engine_texts = [text for kind, text, _ in seen if kind == "engine"]
    scored = [(spoken, caption) for kind, spoken, caption in seen if kind == "scorer"]
    assert len(engine_texts) == len(scored) == 10 * len(phrases)
    for text, (spoken, caption) in zip(engine_texts, scored, strict=True):
        assert spoken == text == spell_numbers(apply_lexicon(caption))
    assert any(spoken != caption for spoken, caption in scored)


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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1960", "mil novecientos sesenta"),
        ("2.847", "dos mil ochocientos cuarenta y siete"),
        ("1.500.000", "un millón quinientos mil"),
        ("9,5", "nueve coma cinco"),
        ("0,25", "cero coma veinticinco"),
        ("50%", "cincuenta por ciento"),
        ("50 %", "cincuenta por ciento"),
        ("3 km", "tres km"),
        ("12.5", "doce coma cinco"),
        ("año 2026", "año dos mil veintiséis"),
        ("el 1 de mayo", "el uno de mayo"),
        ("100", "cien"),
        ("101", "ciento uno"),
        ("1.001", "mil uno"),
        ("21", "veintiuno"),
        ("31 personas", "treinta y una personas"),
        ("ñandú sin cifras", "ñandú sin cifras"),
        (
            "999.999.999.999",
            "novecientos noventa y nueve mil novecientos noventa y nueve"
            " millones novecientos noventa y nueve mil novecientos noventa y nueve",
        ),
        ("1.000.000.000.000", "1.000.000.000.000"),
        ("1,2.3", "1,2.3"),
    ],
)
def test_spell_numbers_table(raw: str, expected: str) -> None:
    assert spell_numbers(raw) == expected
    assert norm_words(raw) == norm_words(spell_numbers(raw))


def test_spell_numbers_lang_and_preservation() -> None:
    original = "¡En 1960, Valdivia tuvo 9,5 grados! Ñandú."
    assert spell_numbers(original, "en") == original
    assert spell_numbers("  Qué tal, Ñandú.  ") == "  Qué tal, Ñandú.  "
    assert spell_numbers("1960,") == "mil novecientos sesenta,"
    assert norm_words("3,5") == ["tres", "coma", "cinco"]
    assert norm_words("12.34") == ["doce", "coma", "treinta", "y", "cuatro"]
    assert norm_words("1.500") == ["mil", "quinientos"]
    assert norm_words("50%") == ["cincuenta", "por", "ciento"]
    assert norm_words("ñandú") == ["ñandu"]
    assert norm_words("") == []


def test_lexicon_case_sensitive() -> None:
    assert apply_lexicon("Rapa rapa RAPA", {"Rapa": "Isla"}) == "Isla rapa RAPA"


def test_render_expands_numbers_but_keeps_caption(tmp_path: Path) -> None:
    seen: list[str] = []

    class RecordingEngine(FakeEngine):
        def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
            seen.append(text)
            return super().generate(text, take)

    script = _script()
    scene = script.scenes[0]
    script = VideoScript(
        title=script.title,
        aspect=script.aspect,
        language=script.language,
        music=script.music,
        voice=script.voice,
        scenes=(
            SceneSpec(
                id=scene.id,
                type=scene.type,
                title=scene.title,
                sub=scene.sub,
                say="En 1960 hubo 9,5 grados",
                caption="En 1960 hubo 9,5 grados",
                query=scene.query,
                prompt=scene.prompt,
                avoid=scene.avoid,
            ),
        ),
    )
    result = render_script(script, tmp_path, RecordingEngine(), FakeScorer([0.01]))
    spoken = "En mil novecientos sesenta hubo nueve coma cinco grados"
    assert seen == [spoken]
    assert result["lines"]["fact-1"]["spoken"] == spoken
    assert result["lines"]["fact-1"]["text"] == "En 1960 hubo 9,5 grados"
    assert result["per_check"]["wer_threshold"] == 0.2


def test_cache_keys_separate_language_voice_and_speed() -> None:
    original = Take(1, "F1", 1.2, lang="es")
    keys = [
        line_cache_key("hola", original, "a" * 64),
        line_cache_key("hola", Take(1, "F1", 1.2, lang="en"), "a" * 64),
        line_cache_key("hola", Take(1, "F2", 1.2, lang="es"), "a" * 64),
        line_cache_key("hola", Take(1, "F1", 1.05, lang="es"), "a" * 64),
    ]
    assert len(set(keys)) == len(keys)


def test_silent_take_retries_and_never_reports_ok(tmp_path: Path) -> None:
    class SilentThenValid(FakeEngine):
        def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
            self.takes.append(take)
            if len(self.takes) == 1:
                return np.zeros(3200, dtype=np.float32), 16000
            return np.full(3200, 0.1, dtype=np.float32), 16000

    engine = SilentThenValid()
    report = render_script(_script(), tmp_path, engine, FakeScorer([0.04]))
    assert len(engine.takes) == 2
    assert report["lines"]["fact-1"]["best_take"] == 2
    assert report["lines"]["fact-1"]["status"] == "ok"

    class AlwaysSilent(FakeEngine):
        def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
            self.takes.append(take)
            return np.zeros(1600, dtype=np.float32), 16000

    other = render_script(_script(), tmp_path / "all-silent", AlwaysSilent(), FakeScorer([]))
    assert other["lines"]["fact-1"]["status"] == "invalid_audio"
    assert other["lines"]["fact-1"]["duration_s"] == 0
    assert other["lines"]["fact-1"]["takes"] == 3


def test_stale_report_is_removed_before_render(tmp_path: Path) -> None:
    (tmp_path / "voice.json").write_text("stale")

    class CrashingEngine(FakeEngine):
        def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
            raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        render_script(_script(), tmp_path, CrashingEngine(), FakeScorer([0.05]))
    assert not (tmp_path / "voice.json").exists()


def test_trim_preserves_pads_and_fade() -> None:
    audio = np.r_[np.zeros(3200), np.ones(1600) * 0.1, np.zeros(3200)].astype(np.float32)
    trimmed = trim_silence(audio, 16000)
    assert 4480 <= len(trimmed) <= 4800
    assert trimmed[0] == 0
    assert trimmed[1] >= trimmed[0]
    assert trimmed[64] == pytest.approx(0.0)  # preserved pre-roll before voice
    assert len(trim_silence(np.zeros(1000, dtype=np.float32), 16000)) == 0


def test_default_scorer_ensures_pinned_phoneme_model(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        models,
        "model_spec",
        lambda name: {"assets": [{"sha256": "a" * 64}]},
    )
    monkeypatch.setattr(
        models,
        "ensure_model",
        lambda name: (calls.append(name), tmp_path)[1],
    )

    class FakePerScorer:
        def __init__(self, directory: Path):
            calls.append(directory)

    monkeypatch.setattr(voice, "PerScorer", FakePerScorer)
    result = voice._default_scorer()
    assert isinstance(result, FakePerScorer)
    assert calls == ["w2v2-phon", tmp_path]


def test_default_scorer_unavailable_reason_is_truthful(monkeypatch) -> None:
    monkeypatch.setattr(
        models,
        "model_spec",
        lambda name: {"assets": [{"sha256": "a" * 64}]},
    )

    def unavailable(_name: str):
        raise models.ModelUnavailable("model missing offline")

    monkeypatch.setattr(models, "ensure_model", unavailable)

    class FakeWhisper:
        def __init__(self, lang: str, reason: str):
            self.reason = reason

    monkeypatch.setattr(voice, "WhisperScorer", FakeWhisper)
    assert voice._default_scorer().reason == ("per_model_unavailable:model missing offline")


def test_whisper_scorer_uses_script_language(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, values, **kwargs):
            calls.append(kwargs)
            return [], None

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    scorer = voice.WhisperScorer(lang="fr")
    result = scorer.score("bonjour", "bonjour", np.ones(1600), 16000)
    assert calls[0]["language"] == "fr"
    assert result.asr == ""
