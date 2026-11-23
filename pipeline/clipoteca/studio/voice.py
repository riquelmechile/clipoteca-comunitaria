"""Supertonic voice rendering with retryable, injectable quality scoring."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import models
from .script import VideoScript, parse_video_script
from .textnorm import DEFAULT_LEXICON, apply_lexicon, norm_words, wer

VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]
JITTER = [1.0, 0.96, 1.04, 0.93]
ALGORITHM_VERSION = "m26-v1"
DEFAULT_VOICE = "F1"
DEFAULT_SPEED = 1.2
NUM_STEPS = 8
PER_MAX = 0.12
LINE_WER_MAX = 0.2


@dataclass(frozen=True, slots=True)
class Take:
    index: int
    voice: str
    speed: float
    lang: str = "es"
    num_steps: int = NUM_STEPS


@dataclass(frozen=True, slots=True)
class Score:
    per: float | None = None
    wer: float | None = None
    asr: str | None = None


class Engine(Protocol):
    model_sha: str

    def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]: ...


class Scorer(Protocol):
    per_enabled: bool
    reason: str

    def score(
        self,
        spoken: str,
        caption: str,
        samples: np.ndarray,
        sample_rate: int,
    ) -> Score: ...


def _find_model_file(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Supertonic asset {name} not found uniquely")
    return matches[0]


class SupertonicEngine:
    """Lazy sherpa-onnx Supertonic adapter."""

    model_sha = "82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427"

    def __init__(self, model_dir: str | Path | None = None):
        import sherpa_onnx as so

        self._so = so
        root = models.ensure_model("supertonic3") if model_dir is None else Path(model_dir)
        supertonic = so.OfflineTtsSupertonicModelConfig(
            duration_predictor=str(_find_model_file(root, "duration_predictor.int8.onnx")),
            text_encoder=str(_find_model_file(root, "text_encoder.int8.onnx")),
            vector_estimator=str(_find_model_file(root, "vector_estimator.int8.onnx")),
            vocoder=str(_find_model_file(root, "vocoder.int8.onnx")),
            tts_json=str(_find_model_file(root, "tts.json")),
            unicode_indexer=str(_find_model_file(root, "unicode_indexer.bin")),
            voice_style=str(_find_model_file(root, "voice.bin")),
        )
        config = so.OfflineTtsConfig(
            model=so.OfflineTtsModelConfig(
                supertonic=supertonic,
                num_threads=os.cpu_count() or 4,
            ),
            max_num_sentences=1,
        )
        self._tts = so.OfflineTts(config)

    def generate(self, text: str, take: Take) -> tuple[np.ndarray, int]:
        generation = self._so.GenerationConfig()
        generation.sid = VOICES.index(take.voice)
        generation.speed = take.speed
        generation.extra = {"lang": take.lang}
        generation.num_steps = take.num_steps
        audio = self._tts.generate(text, generation)
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)


class WhisperScorer:
    per_enabled = False
    reason = "per_model_unpinned"

    def __init__(self):
        from faster_whisper import WhisperModel

        spec = models.model_spec("whisper")
        compute = os.environ.get("CLIPOTECA_WHISPER_COMPUTE", spec["compute_type"])
        self._model = WhisperModel(
            spec["repo"],
            compute_type=compute,
            revision=spec["revision"],
        )

    def score(self, spoken: str, caption: str, samples: np.ndarray, sample_rate: int) -> Score:
        if sample_rate != 16000:
            samples = _resample(samples, sample_rate, 16000)
        segments, _ = self._model.transcribe(
            samples,
            language="es",
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        transcript = " ".join(segment.text.strip() for segment in segments).strip()
        return Score(wer=wer(norm_words(caption), norm_words(transcript)), asr=transcript)


class PerScorer:
    per_enabled = True
    reason = "available"

    def __init__(self, model_dir: str | Path):
        from .phon import Worker

        self._worker = Worker(model_dir)

    def score(self, spoken: str, caption: str, samples: np.ndarray, sample_rate: int) -> Score:
        values = samples if sample_rate == 16000 else _resample(samples, sample_rate, 16000)
        value, _, hypothesis = self._worker.score(spoken, values)
        return Score(per=value, asr=hypothesis)


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or not len(samples):
        return np.asarray(samples, dtype=np.float32)
    count = round(len(samples) * target_rate / source_rate)
    old = np.linspace(0.0, 1.0, len(samples), endpoint=False)
    new = np.linspace(0.0, 1.0, count, endpoint=False)
    return np.interp(new, old, samples).astype(np.float32)


def trim_silence(
    samples: np.ndarray,
    sample_rate: int,
    threshold: float = 0.008,
) -> np.ndarray:
    """Trim 10 ms RMS silence, preserving pads and a 4 ms fade-in."""
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not len(values):
        return values
    window = max(1, round(sample_rate * 0.01))
    rms = [
        float(np.sqrt(np.mean(values[i : i + window] ** 2))) for i in range(0, len(values), window)
    ]
    active = [index for index, value in enumerate(rms) if value >= threshold]
    if not active:
        return values[:0]
    start = max(0, active[0] * window - round(sample_rate * 0.1))
    end = min(len(values), (active[-1] + 1) * window + round(sample_rate * 0.08))
    result = values[start:end].copy()
    fade = min(len(result), round(sample_rate * 0.004))
    if fade:
        result[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
    return result


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(str(temp_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm.tobytes())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("cached voice WAV must be mono PCM16")
        rate = source.getframerate()
        data = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    return data.astype(np.float32) / 32767.0, rate


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def line_cache_key(text: str, take: Take, model_sha: str) -> str:
    payload = {
        "text": text,
        "voice": take.voice,
        "speed": take.speed,
        "steps": take.num_steps,
        "model_sha": model_sha,
        "algorithm": ALGORITHM_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _metric(score: Score) -> tuple[float, float]:
    if score.per is not None:
        return score.per, PER_MAX
    return score.wer if score.wer is not None else float("inf"), LINE_WER_MAX


def _default_scorer() -> Scorer:
    enabled, _ = models.per_model_status()
    if enabled:
        return PerScorer(models.cache_dir() / "w2v2-phon")
    return WhisperScorer()


def render_script(
    script: VideoScript,
    out: str | Path,
    engine: Engine,
    scorer: Scorer,
    *,
    max_tries: int = 3,
) -> dict[str, Any]:
    """Render all spoken scenes, always keeping the best take."""
    output = Path(out)
    audio_dir = output / "audio"
    cache = output / ".line-cache"
    cache.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    lines: dict[str, dict[str, Any]] = {}
    durations: dict[str, float] = {}
    # Script entries extend (and may override) the built-in spoken-form lexicon.
    lexicon = {**DEFAULT_LEXICON, **dict(script.voice.lexicon)}
    for scene in script.scenes:
        spoken = apply_lexicon(scene.say, lexicon)
        best: tuple[float, np.ndarray, int, Score, int] | None = None
        take_scores: list[dict[str, Any]] = []
        for index in range(max_tries):
            take = Take(
                index=index + 1,
                voice=script.voice.voice,
                speed=script.voice.speed * JITTER[index % len(JITTER)],
                lang=script.language,
            )
            key = line_cache_key(spoken, take, engine.model_sha)
            cached = cache / f"{key}.wav"
            if cached.exists():
                samples, sample_rate = _read_wav(cached)
            else:
                samples, sample_rate = engine.generate(spoken, take)
                samples = trim_silence(samples, sample_rate)
                _write_wav(cached, samples, sample_rate)
            score = scorer.score(spoken, scene.caption, samples, sample_rate)
            metric, threshold = _metric(score)
            take_scores.append({"take": index + 1, **asdict(score)})
            if best is None or metric < best[0]:
                best = (metric, samples, sample_rate, score, index + 1)
            if metric <= threshold:
                break
        assert best is not None
        metric, samples, sample_rate, score, best_take = best
        _, threshold = _metric(score)
        wav_path = audio_dir / f"{scene.id}.wav"
        _write_wav(wav_path, samples, sample_rate)
        duration = len(samples) / sample_rate
        durations[scene.id] = duration
        lines[scene.id] = {
            "text": scene.say,
            "spoken": spoken,
            "per": score.per,
            "per_takes": [item["per"] for item in take_scores if item["per"] is not None],
            "wer": score.wer,
            "takes": len(take_scores),
            "best_take": best_take,
            "asr": score.asr,
            "duration_s": round(duration, 6),
            "status": "ok" if metric <= threshold else "below_threshold",
        }
    total = sum(durations.values())
    elapsed = time.monotonic() - started
    report = {
        "engine": "sherpa-onnx-supertonic3",
        "model": {"name": "supertonic3", "sha256": engine.model_sha},
        "voice": script.voice.voice,
        "speed": script.voice.speed,
        "lang": script.language,
        "lines": lines,
        "durations": durations,
        "total_voice_s": round(total, 6),
        "rtf": round(elapsed / total, 6) if total else 0.0,
        "per_check": {
            "enabled": scorer.per_enabled,
            "threshold": PER_MAX,
            "reason": scorer.reason,
        },
    }
    _atomic_json(output / "voice.json", report)
    return report


def _sample(out: Path) -> None:
    fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/m26/frases.json"
    phrases = json.loads(fixture.read_text(encoding="utf-8"))
    engine = SupertonicEngine()
    scorer = _default_scorer()
    rows = ["| Voz | PER medio | WER medio | Peor frase | RTF |", "|---|---:|---:|---|---:|"]
    result: dict[str, Any] = {"voices": {}}
    for voice_name in VOICES:
        records = []
        for index, phrase in enumerate(phrases, 1):
            take = Take(index=1, voice=voice_name, speed=DEFAULT_SPEED)
            started = time.monotonic()
            spoken = apply_lexicon(phrase)
            samples, rate = engine.generate(spoken, take)
            samples = trim_silence(samples, rate)
            score = scorer.score(spoken, phrase, samples, rate)
            elapsed = time.monotonic() - started
            _write_wav(out / "audio" / f"{voice_name}-{index:02}.wav", samples, rate)
            records.append(
                {
                    "phrase": index,
                    **asdict(score),
                    "rtf": elapsed / (len(samples) / rate),
                }
            )
        result["voices"][voice_name] = records
        pers = [record["per"] for record in records if record["per"] is not None]
        wers = [record["wer"] for record in records if record["wer"] is not None]
        worst = max(
            records,
            key=lambda record: (record["per"] if record["per"] is not None else record["wer"] or 0),
        )
        mean_per = f"{sum(pers) / len(pers):.3f}" if pers else "n/a"
        mean_wer = f"{sum(wers) / len(wers):.3f}" if wers else "n/a"
        mean_rtf = sum(record["rtf"] for record in records) / len(records)
        rows.append(
            f"| {voice_name} | {mean_per} | {mean_wer} | {worst['phrase']} | {mean_rtf:.3f} |",
        )
    result["table_markdown"] = "\n".join(rows) + "\n"
    _atomic_json(out / "sample.json", result)
    _atomic_text(out / "sample.md", result["table_markdown"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.voice")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render")
    render.add_argument("--script", type=Path, required=True)
    render.add_argument("--out", type=Path, required=True)
    sample = subparsers.add_parser("sample")
    sample.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "sample":
        _sample(args.out)
    else:
        data = json.loads(args.script.read_text(encoding="utf-8"))
        render_script(parse_video_script(data), args.out, SupertonicEngine(), _default_scorer())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
