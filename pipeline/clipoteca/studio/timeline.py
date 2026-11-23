"""Real-audio Studio timeline and injectable Whisper word alignment."""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .script import MAX_VIDEO_SECONDS, VideoScript, parse_video_script, script_id
from .textnorm import norm_words, wer


def _word_values(segs: Any) -> list[tuple[str, float, float]]:
    joined: list[tuple[str, float, float]] = []
    for segment in segs:
        for word in segment.words or []:
            token = word.word.strip()
            start, end = float(word.start), float(word.end)
            if token == "%" and joined:
                last, beginning, _ = joined[-1]
                joined[-1] = (last + "%", beginning, end)
            elif (
                joined and re.match(r"^[.,]\d", token) and re.search(r"\d$", joined[-1][0].strip())
            ):
                last, beginning, _ = joined[-1]
                joined[-1] = (last + token, beginning, end)
            else:
                joined.append((word.word, start, end))
    return joined


def word_timings(
    samples16k: np.ndarray,
    caption: str,
    lang: str,
    transcriber: Any,
) -> tuple[list[list[Any]], str, float, str]:
    """Align caption words to real Whisper times, interpolating unmatched spans."""
    duration = len(samples16k) / 16000
    if hasattr(transcriber, "transcribe"):
        result = transcriber.transcribe(
            samples16k,
            language=lang,
            word_timestamps=True,
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
    else:
        result = transcriber(samples16k, lang)
    segments = result[0] if isinstance(result, tuple) else result
    heard = _word_values(segments)
    asr = " ".join(word.strip() for word, _, _ in heard)
    line_wer = wer(norm_words(caption, lang), norm_words(asr, lang))
    caption_words = caption.split()
    if not caption_words:
        return [], asr, round(line_wer, 6), "proporcional" if not heard else "whisper+align"
    positions: list[tuple[float, float] | None] = [None] * len(caption_words)
    if len(heard) == len(caption_words):
        positions = [(start, end) for _, start, end in heard]
        timing = "whisper"
    elif heard:
        timing = "whisper+align"

        def key(word: str) -> str:
            return "".join(norm_words(word, lang))

        matcher = difflib.SequenceMatcher(
            a=[key(word) for word in caption_words],
            b=[key(word) for word, _, _ in heard],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    positions[i1 + offset] = (heard[j1 + offset][1], heard[j1 + offset][2])
            elif tag == "replace" and j1 < j2 and i1 < i2:
                start, end = heard[j1][1], heard[j2 - 1][2]
                count = i2 - i1
                for offset in range(count):
                    positions[i1 + offset] = (
                        start + (end - start) * offset / count,
                        start + (end - start) * (offset + 1) / count,
                    )
        index = 0
        while index < len(positions):
            if positions[index] is not None:
                index += 1
                continue
            end_index = index
            while end_index < len(positions) and positions[end_index] is None:
                end_index += 1
            begin = positions[index - 1][1] if index and positions[index - 1] else heard[0][1]
            end = (
                positions[end_index][0]
                if end_index < len(positions) and positions[end_index]
                else heard[-1][2]
            )
            count = end_index - index
            for offset in range(count):
                positions[index + offset] = (
                    begin + (end - begin) * offset / count,
                    begin + (end - begin) * (offset + 1) / count,
                )
            index = end_index
    else:
        timing = "proporcional"
        lengths = [
            len(word) + (3 if word.endswith((".", ",", ":", ";", "?", "!")) else 0)
            for word in caption_words
        ]
        total = sum(lengths)
        cursor = 0.0
        for i, length in enumerate(lengths):
            end = cursor + duration * length / total
            positions[i] = (cursor, end)
            cursor = end
    result_words: list[list[Any]] = []
    previous = 0.0
    for word, position in zip(caption_words, positions, strict=True):
        assert position is not None
        start = min(duration, max(previous, float(position[0]), 0.0))
        end = min(duration, max(start, float(position[1])))
        start = round(start, 3)
        end = round(max(start, end), 3)
        result_words.append([word, start, end])
        previous = end
    return result_words, asr, round(line_wer, 6), timing


def _read_wav_16k(path: Path) -> tuple[np.ndarray, float]:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
            raise ValueError(f"{path.name}: WAV must be mono PCM16")
        rate = stream.getframerate()
        count = stream.getnframes()
        values = np.frombuffer(stream.readframes(count), dtype="<i2").astype(np.float32)
    if rate <= 0:
        raise ValueError(f"{path.name}: invalid sample rate")
    duration = count / rate
    values /= 32767.0
    if rate != 16000 and len(values):
        new_count = round(len(values) * 16000 / rate)
        values = np.interp(
            np.arange(new_count) / 16000,
            np.arange(len(values)) / rate,
            values,
        ).astype(np.float32)
    return values, duration


def _atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def build_timeline(script: VideoScript, voice_dir: str | Path, transcriber: Any) -> dict[str, Any]:
    """Quantize real WAV scene durations to beats, without target stretching."""
    folder = Path(voice_dir)
    report_path = folder / "voice.json"
    if not report_path.is_file():
        raise ValueError("voice.json not found")
    voice_report = json.loads(report_path.read_text(encoding="utf-8"))
    beat = 60 / script.music.bpm
    scenes: list[dict[str, Any]] = []
    current = 0.0
    for scene in script.scenes:
        line = voice_report.get("lines", {}).get(scene.id)
        if not isinstance(line, dict) or line.get("status") in {
            "missing",
            "not_found",
            "nonexistent",
            "invalid_audio",
        }:
            raise ValueError(f"voice.json missing scene {scene.id}")
        wav_path = folder / "audio" / f"{scene.id}.wav"
        if not wav_path.is_file():
            raise FileNotFoundError(f"missing WAV for scene {scene.id}: {wav_path}")
        values, duration = _read_wav_16k(wav_path)
        words, asr, line_wer, timing = word_timings(
            values,
            scene.caption,
            script.language,
            transcriber,
        )
        extra = 1.2 if scene.type == "outro" else 0.6
        minimum = 3.5 if scene.type == "outro" else 4 * beat
        pulses = max(math.ceil((duration + extra) / beat), math.ceil(minimum / beat))
        length = round(pulses * beat, 6)
        scenes.append(
            {
                "id": scene.id,
                "type": scene.type,
                "title": scene.title,
                "sub": scene.sub,
                "caption": scene.caption,
                "dur": round(duration, 6),
                "words": words,
                "asr": asr,
                "line_wer": line_wer,
                "timing": timing,
                "start": round(current, 6),
                "len": length,
                "vo": round(current + 0.3, 6),
            }
        )
        current += length
    if current > MAX_VIDEO_SECONDS + 1e-6:
        raise ValueError(
            f"the voiced script lasts {current:.1f} s; the maximum is {MAX_VIDEO_SECONDS:g} s"
        )
    result = {
        "script_id": script_id(script),
        "bpm": script.music.bpm,
        "beat": round(beat, 6),
        "scenes": scenes,
        "total": round(current, 6),
        "cuts": [scene["start"] for scene in scenes[1:]],
    }
    _atomic_json(folder / "timeline.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.timeline")
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--voice", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    script = parse_video_script(json.loads(args.script.read_text(encoding="utf-8")))
    try:
        from faster_whisper import WhisperModel
        from . import models

        spec = models.model_spec("whisper")
        transcriber = WhisperModel(
            spec["repo"], revision=spec["revision"], compute_type=spec["compute_type"]
        )
        timeline = build_timeline(script, args.voice, transcriber)
        _atomic_json(args.out, timeline)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
