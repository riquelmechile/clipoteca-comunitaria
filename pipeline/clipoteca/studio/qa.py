"""M31 publication QA: fourteen independent, fail-closed video gates."""

from __future__ import annotations

import argparse
import hashlib
import difflib
import json
import math
import os
import re
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np

from . import checks
from .compose import CONFIG, _atomic, _json_bytes
from .script import MAX_VIDEO_SECONDS, parse_video_script, script_id
from .textnorm import DEFAULT_LEXICON, apply_lexicon, norm_words, wer

MAX_WER = 0.12
SYNC_MEAN_MS = 120
SYNC_P95_MS = 350
TARGET_LUFS = -14.0
LUFS_TOL = 1.0
MAX_TRUE_PEAK_DBFS = -0.9
MAX_SIZE_MB = 100
MAX_DURATION_S = MAX_VIDEO_SECONDS
DURATION_TOL = 0.35
MIN_SHORT_SIDE = 720
DEFAULT_TIMEOUT = 180

CHECK_NAMES = (
    "hyperframes_check",
    "layout_playwright",
    "file_probe",
    "decode_clean",
    "blackdetect",
    "freezedetect",
    "silencedetect",
    "loudness_ebur128",
    "whisper_wer",
    "subtitle_sync",
    "clips_all_scenes",
    "clips_unique",
    "clips_resolution",
    "clips_black_freeze",
)


class QAInputError(ValueError):
    """Required top-level QA inputs are absent or malformed."""


@dataclass(frozen=True)
class HeardWord:
    word: str
    start: float
    end: float


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT):
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"command unavailable or timed out: {argv[0]}") from exc


def _checked(process: subprocess.CompletedProcess[str], tool: str) -> str:
    if process.returncode:
        raise RuntimeError(f"{tool} exited with status {process.returncode}")
    return process.stdout + "\n" + process.stderr


def _safe_error(error: Exception) -> str:
    message = str(error)
    # Never leak private absolute paths, tool output or external URLs in the report.
    message = re.sub(r"(?:[A-Za-z]:)?/(?:[^ \n\t:]+/)*[^ \n\t:]+", "<path>", message)
    message = re.sub(r"https?://\S+", "<url>", message)
    return f"{type(error).__name__}: {message[:200]}"


def _gate(ok: bool, **details: Any) -> dict[str, Any]:
    return {"pass": bool(ok), **details}


def hyperframes_check(hf: Path, *, runner: Runner = _run) -> dict[str, Any]:
    result = runner(
        ["npx", "--yes", "hyperframes@0.9.36", "check"],
        cwd=hf,
        timeout=DEFAULT_TIMEOUT,
    )
    output = result.stdout + "\n" + result.stderr
    errors = sum(int(match) for match in re.findall(r"(\d+)\s+error\(s\)", output))
    contrast = len(re.findall(r"✗[^\n]*need\s+3:1", output, re.IGNORECASE))
    return _gate(
        result.returncode == 0 and "Check passed" in output and errors == 0,
        errors=errors,
        contrast_warnings=contrast,
    )


_LAYOUT_JS = r"""([times,safe])=>{
  const tl=window.__timelines.main, R=[]; let M=0;
  const vis=(el,t)=>{let c=el.closest('.clip');while(c){const s=+c.dataset.start,d=+c.dataset.duration;if(t<s||t>=s+d)return false;c=c.parentElement&&c.parentElement.closest('.clip');}return true};
  const box=e=>{const rg=document.createRange(); rg.selectNodeContents(e); return rg.getBoundingClientRect();};
  const ov=(a,b,m=2)=>!(a.right<=b.left+m||b.right<=a.left+m||a.bottom<=b.top+m||b.bottom<=a.top+m);
  for(const t of times){ tl.seek(t,false);
    const els=[...document.querySelectorAll('.qa-text')].filter(e=>vis(e,t)&&+getComputedStyle(e).opacity>0.05&&e.innerText.trim());
    const okN=n=>{const cs=getComputedStyle(n); if(+cs.opacity<0.99) return false; const m=cs.transform; if(!m||m==='none') return true; const v=m.match(/matrix\(([^)]+)\)/); if(!v) return true; const [a,b]=v[1].split(',').map(Number); return Math.abs(Math.hypot(a,b)-1)<0.01;};
    const settled=el=>{let p=el; while(p){ if(!okN(p)) return false; p=p.parentElement;} return [...el.querySelectorAll('.tw')].every(okN);};
    for(const el of els){ if(!settled(el)) continue; const r=el.getBoundingClientRect(); let over=el.scrollWidth>el.clientWidth+1;
      el.querySelectorAll('span').forEach(sp=>{const q=sp.getBoundingClientRect(); if(q.width>0&&(q.left<r.left-2||q.right>r.right+2)) over=true;});
      const ri=box(el); const outSafe=ri.left<safe[0]-1||ri.right>safe[2]+1||ri.top<safe[1]-1||ri.bottom>safe[3]+1;
      M++; if(over||outSafe) R.push({t:+t.toFixed(2),text:el.innerText.slice(0,40),overflow:over,outSafe,box:[ri.left,ri.top,ri.right,ri.bottom].map(Math.round)});}
    for(let i=0;i<els.length;i++)for(let j=i+1;j<els.length;j++){ if(els[i].contains(els[j])||els[j].contains(els[i]))continue;
      if(ov(els[i].getBoundingClientRect(),els[j].getBoundingClientRect(),2)) R.push({t:+t.toFixed(2),text:els[i].innerText.slice(0,20)+' / '+els[j].innerText.slice(0,20),overlap:true});}
  } return {checked:times.length,measured:M,problems:R};}"""


def layout_playwright(
    hf: Path,
    timeline: dict[str, Any],
    chunks: list[Any],
    aspect: str,
    *,
    browser_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    width, height = CONFIG[aspect]["size"]
    safe = CONFIG[aspect]["safe"]
    times = [
        round(float(scene["start"]) + float(scene["len"]) * fraction, 6)
        for scene in timeline["scenes"]
        for fraction in (0.35, 0.65)
    ]
    for chunk in chunks:
        if not isinstance(chunk, list) or len(chunk) != 2:
            raise ValueError("chunks.json invalid entry")
        times.append(round(float(chunk[1]) + 0.16, 6))
    # Playwright's import and Chromium launch are lazy and isolated to this gate.
    if browser_factory is None:
        from playwright.sync_api import sync_playwright

        browser_factory = sync_playwright
    with browser_factory() as browser_api:
        kwargs: dict[str, Any] = {"headless": True, "args": ["--allow-file-access-from-files"]}
        external_chromium = os.environ.get("HYPERFRAMES_BROWSER_PATH")
        if external_chromium:
            kwargs["executable_path"] = external_chromium
        browser = browser_api.chromium.launch(**kwargs)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route(
                "**/*",
                lambda route: route.continue_()
                if route.request.url.startswith("file://")
                else route.abort(),
            )
            page.goto((hf / "index.html").resolve().as_uri(), wait_until="load")
            page.evaluate("() => document.fonts.ready")
            measured = page.evaluate(_LAYOUT_JS, [times, safe])
            problems = measured["problems"]
            texts = int(measured.get("measured", 0))
            return _gate(
                not problems and texts > 0,
                checked=int(measured["checked"]),
                measured=texts,
                problems=problems[:25],
            )
        finally:
            browser.close()


def file_probe(
    video: Path,
    total: float,
    size: tuple[int, int],
    *,
    runner: Runner = _run,
) -> dict[str, Any]:
    output = _checked(
        runner(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
            timeout=60,
        ),
        "ffprobe",
    )
    probe = json.loads(output)
    streams = probe["streams"]
    if len([stream for stream in streams if stream["codec_type"] == "video"]) != 1:
        return _gate(False, error="expected exactly one video stream")
    if len([stream for stream in streams if stream["codec_type"] == "audio"]) != 1:
        return _gate(False, error="expected exactly one audio stream")
    v = next(x for x in streams if x["codec_type"] == "video")
    a = next(x for x in streams if x["codec_type"] == "audio")
    duration = float(probe["format"]["duration"])
    size_mb = video.stat().st_size / 1_000_000
    ok = (
        v.get("codec_name") == "h264"
        and a.get("codec_name") == "aac"
        and (int(v["width"]), int(v["height"])) == size
        and math.isfinite(duration)
        and abs(duration - total) < DURATION_TOL
        and duration <= MAX_DURATION_S + DURATION_TOL
        and size_mb < MAX_SIZE_MB
    )
    return _gate(
        ok,
        duration_s=round(duration, 3),
        size_mb=round(size_mb, 3),
        video_codec=v.get("codec_name"),
        audio_codec=a.get("codec_name"),
    )


def decode_clean(video: Path, *, runner: Runner = _run) -> dict[str, Any]:
    result = runner(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
        timeout=DEFAULT_TIMEOUT,
    )
    return _gate(
        result.returncode == 0 and not result.stderr.strip(), stderr_empty=not result.stderr.strip()
    )


def blackdetect(video: Path, *, runner: Runner = _run) -> dict[str, Any]:
    output = _checked(
        runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(video),
                "-vf",
                "blackdetect=d=0.1:pix_th=0.10",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=DEFAULT_TIMEOUT,
        ),
        "ffmpeg",
    )
    starts = [float(x) for x in re.findall(r"black_start:\s*([\d.]+)", output)]
    return _gate(not starts, starts=starts[:20])


def freezedetect(video: Path, *, runner: Runner = _run) -> dict[str, Any]:
    output = _checked(
        runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(video),
                "-vf",
                "freezedetect=n=0.001:d=1.0",
                "-an",
                "-f",
                "null",
                "-",
            ],
            timeout=DEFAULT_TIMEOUT,
        ),
        "ffmpeg",
    )
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", output)]
    return _gate(not starts, starts=starts[:20])


def silencedetect(
    video: Path,
    total: float,
    *,
    runner: Runner = _run,
) -> dict[str, Any]:
    output = _checked(
        runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(video),
                "-af",
                "silencedetect=n=-45dB:d=0.8",
                "-vn",
                "-f",
                "null",
                "-",
            ],
            timeout=DEFAULT_TIMEOUT,
        ),
        "ffmpeg",
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", output)]
    illegal = [round(start, 3) for start in starts if start < total - 1.3]
    return _gate(not illegal, starts=starts[:20], early_starts=illegal[:20])


def loudness_ebur128(video: Path, *, runner: Runner = _run) -> dict[str, Any]:
    output = _checked(
        runner(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(video),
                "-filter_complex",
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            timeout=DEFAULT_TIMEOUT,
        ),
        "ffmpeg",
    )
    summary = output.rsplit("Summary:", 1)[-1] if "Summary:" in output else ""
    integrated = re.search(r"\bI:\s*(-?[\d.]+)\s*LUFS", summary)
    peak = re.search(r"\bPeak:\s*(-?[\d.]+)\s*dBFS", summary)
    if integrated is None or peak is None:
        raise ValueError("ebur128 Summary missing I or true Peak")
    lufs, peak_db = float(integrated.group(1)), float(peak.group(1))
    return _gate(
        math.isfinite(lufs)
        and abs(lufs - TARGET_LUFS) <= LUFS_TOL
        and math.isfinite(peak_db)
        and peak_db <= MAX_TRUE_PEAK_DBFS,
        lufs=lufs,
        true_peak_dbfs=peak_db,
    )


def _word_obj(obj: Any) -> HeardWord:
    if isinstance(obj, dict):
        value = HeardWord(str(obj["word"]), float(obj["start"]), float(obj["end"]))
    elif isinstance(obj, (tuple, list)) and len(obj) == 3:
        value = HeardWord(str(obj[0]), float(obj[1]), float(obj[2]))
    else:
        value = HeardWord(str(obj.word), float(obj.start), float(obj.end))
    if not (
        math.isfinite(value.start) and math.isfinite(value.end) and 0 <= value.start <= value.end
    ):
        raise ValueError("Whisper word timestamps invalid")
    return value


def _default_transcribe(samples: np.ndarray, lang: str):
    from faster_whisper import WhisperModel
    from huggingface_hub import snapshot_download

    models = json.loads((Path(__file__).parent / "models.json").read_text())
    spec = models["models"]["whisper"]
    directory = snapshot_download(repo_id=spec["repo"], revision=spec["revision"])
    model = WhisperModel(directory, device="cpu", compute_type=spec["compute_type"])
    segments, _metadata = model.transcribe(
        samples,
        language=lang,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"threshold": 0.3, "speech_pad_ms": 400, "min_silence_duration_ms": 500},
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    return [
        HeardWord(word.word, word.start, word.end)
        for segment in segments
        for word in (segment.words or [])
    ]


def transcribe_once(
    video: Path,
    lang: str,
    *,
    transcriber: Callable[..., Any] | None = None,
    runner: Runner = _run,
) -> list[HeardWord]:
    with tempfile.TemporaryDirectory(prefix="clipoteca-qa-") as folder:
        source = Path(folder) / "audio.wav"
        _checked(
            runner(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(video),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-acodec",
                    "pcm_s16le",
                    str(source),
                ],
                timeout=DEFAULT_TIMEOUT,
            ),
            "ffmpeg",
        )
        if not source.exists():
            raise ValueError("audio extraction did not produce WAV")
        with wave.open(str(source), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getframerate() != 16000:
                raise ValueError("decoded audio is not 16 kHz mono")
            if wav.getsampwidth() != 2:
                raise ValueError("decoded audio is not PCM16")
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
            samples = audio.astype(np.float32) / 32768.0
        callback = transcriber or _default_transcribe
        result = callback(samples, lang)
        if isinstance(result, tuple) and len(result) == 2:
            result = result[0]
        return [_word_obj(x) for x in result]


def whisper_wer(
    timeline: dict[str, Any],
    lang: str,
    heard: list[HeardWord],
    lexicon: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compare what was heard with the caption as written and as the voice speaks it.

    The voice reads the caption after the lexicon ("km/h" -> "kilómetros por hora"), while
    Whisper may transcribe either form, so the lower of both error rates decides.
    """
    reference = " ".join(str(scene["caption"]) for scene in timeline["scenes"])
    spoken = apply_lexicon(reference, {**DEFAULT_LEXICON, **dict(lexicon or {})})
    actual = " ".join(x.word.strip() for x in heard)
    got = norm_words(actual, lang)
    error = min(wer(norm_words(reference, lang), got), wer(norm_words(spoken, lang), got))
    return _gate(error <= MAX_WER, wer=round(error, 6), text=actual[:400])


def subtitle_sync(
    timeline: dict[str, Any],
    lang: str,
    heard: list[HeardWord],
) -> dict[str, Any]:
    caps = [
        (word, float(scene["vo"]) + a, float(scene["vo"]) + b)
        for scene in timeline["scenes"]
        for word, a, b in scene["words"]
    ]

    def normalize(value: str) -> str:
        return "".join(norm_words(value, lang))

    matcher = difflib.SequenceMatcher(
        None,
        [normalize(c[0]) for c in caps],
        [normalize(w.word) for w in heard],
        autojunk=False,
    )
    differences: list[float] = []
    worst: list[str] = []
    for block in matcher.get_matching_blocks():
        for index in range(block.size):
            if not normalize(caps[block.a + index][0]):
                continue
            actual_index = block.b + index
            caption = caps[block.a + index]
            word = heard[actual_index]
            after_gap = (
                actual_index == 0
                or word.start - heard[actual_index - 1].end > 0.25
                or (word.end - word.start) > max(0.7, 2.5 * (caption[2] - caption[1]))
            )
            offset = abs(word.end - caption[2]) if after_gap else abs(word.start - caption[1])
            differences.append(offset)
            if offset > 0.35:
                point = "end" if after_gap else "start"
                worst.append(f"{caption[0]} {point} off {offset:.2f}s")
    values = np.array(differences) if differences else np.array([9.9])
    mean_ms, p95_ms = float(values.mean() * 1000), float(np.percentile(values, 95) * 1000)
    ok = (
        len(differences) >= 0.7 * len(caps)
        and bool(caps)
        and mean_ms <= SYNC_MEAN_MS
        and p95_ms <= SYNC_P95_MS
    )
    return _gate(
        ok,
        matched=len(differences),
        expected=len(caps),
        mean_ms=round(mean_ms, 2),
        p95_ms=round(p95_ms, 2),
        worst=worst[:20],
    )


def _results(timeline: dict[str, Any], scenes: Path) -> dict[str, dict[str, Any]]:
    collected = {}
    for row in timeline["scenes"]:
        scene_id = str(row["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scene_id):
            raise QAInputError("unsafe timeline scene ID")
        source = scenes / scene_id / "result.json"
        if source.is_file():
            collected[scene_id] = json.loads(source.read_text(encoding="utf-8"))
    return collected


def clips_all_scenes(
    timeline: dict[str, Any],
    results: dict[str, dict[str, Any]],
    fit_dir: Path,
    html: str,
) -> dict[str, Any]:
    ids = [str(scene["id"]) for scene in timeline["scenes"]]
    videos = re.findall(r"<video\b[^>]*>", html, re.IGNORECASE)
    valid = len(videos) == len(ids) and not re.search(r"<img\b", html, re.IGNORECASE)
    missing = []
    for ident in ids:
        result = results.get(ident, {})
        if result.get("status") != "ok" or not isinstance(result.get("clip"), dict):
            missing.append(ident)
        if not (fit_dir / f"{ident}.mp4").is_file():
            missing.append(ident)
        if not any(
            re.search(rf'\bid="v_{re.escape(ident)}"', tag)
            and re.search(rf"assets/clips/{re.escape(ident)}\.mp4", tag)
            for tag in videos
        ):
            missing.append(ident)
    return _gate(
        valid and not missing,
        missing=sorted(set(missing)),
        video_count=len(videos),
        expected=len(ids),
    )


def clips_unique(timeline: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Fail closed: a scene without a usable result or pHash was never deduplicated."""
    import imagehash

    invalid: list[str] = []
    for scene in timeline["scenes"]:
        ident = str(scene["id"])
        row = results.get(ident)
        clip = row.get("clip") if isinstance(row, dict) else None
        usable = isinstance(row, dict) and row.get("status") == "ok" and isinstance(clip, dict)
        if usable:
            try:
                imagehash.hex_to_hash(str(clip["phash"]))
                usable = isinstance(clip.get("clip_id"), str) and bool(clip["clip_id"])
            except (KeyError, TypeError, ValueError):
                usable = False
        if not usable:
            invalid.append(ident)
    conflicts = checks.check_global(results)
    return _gate(not conflicts and not invalid, conflicts=conflicts, invalid=invalid)


def clips_resolution(
    timeline: dict[str, Any],
    results: dict[str, dict[str, Any]],
    size: tuple[int, int],
) -> dict[str, Any]:
    width, height = size
    failures = []
    for row in timeline["scenes"]:
        ident = str(row["id"])
        clip = results.get(ident, {}).get("clip", {})
        if not isinstance(clip, dict):
            failures.append(ident)
            continue
        try:
            sw, sh, ow, oh = (int(clip[key]) for key in ("src_w", "src_h", "out_w", "out_h"))
            orient = (sw > sh) == (width > height) if width != height else sw == sh
            accepted = min(sw, sh) >= MIN_SHORT_SIDE or (orient and max(sw, sh) >= 0.53 * max(size))
            if results.get(ident, {}).get("origin") in ("contribution_image", "repo_asset"):
                accepted = max(sw, sh) >= 0.5 * max(size)
            if min(sw, sh, ow, oh) <= 0 or not accepted or (ow, oh) != size:
                failures.append(ident)
        except (ValueError, TypeError, KeyError):
            failures.append(ident)
    return _gate(not failures, invalid=failures)


def clips_black_freeze(
    timeline: dict[str, Any],
    fit_dir: Path,
    *,
    inspector: Callable[..., tuple[list[float], list[float]]] = checks.black_frozen,
) -> dict[str, Any]:
    failures = {}
    for row in timeline["scenes"]:
        ident = str(row["id"])
        try:
            black, frozen = inspector(fit_dir / f"{ident}.mp4")
            if black or frozen:
                failures[ident] = {"black": black[:10], "frozen": frozen[:10]}
        except Exception as exc:
            failures[ident] = {"error": _safe_error(exc)}
    return _gate(not failures, invalid=failures)


def _required(path: Path, label: str) -> Path:
    if not path.is_file():
        raise QAInputError(f"missing {label}")
    return path


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(_required(path, label).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if isinstance(exc, QAInputError):
            raise
        raise QAInputError(f"invalid {label}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_inputs(
    work: Path,
    *,
    video_path: Path | None,
    hf_dir: Path | None,
    chunks_path: Path | None,
    timeline_path: Path | None,
    script_path: Path | None,
    scenes_dir: Path | None,
    fit_dir: Path | None,
) -> dict[str, Any]:
    video = _required(video_path or work / "video.mp4", "video.mp4")
    hf = hf_dir or work / "hf"
    html = _required(hf / "index.html", "hf/index.html").read_text(encoding="utf-8")
    chunks = _read_json(chunks_path or work / "chunks.json", "chunks.json")
    timeline = _read_json(timeline_path or work / "timeline.json", "timeline.json")
    raw_script = _read_json(script_path or work / "guion.json", "guion.json")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
        raise QAInputError("invalid timeline.json")
    if not isinstance(chunks, list):
        raise QAInputError("invalid chunks.json")
    try:
        script = parse_video_script(raw_script)
    except ValueError as exc:
        raise QAInputError("invalid guion.json") from exc
    if script.aspect not in CONFIG:
        raise QAInputError("unsupported aspect")
    if not isinstance(timeline.get("total"), (int, float)):
        raise QAInputError("timeline.total missing")
    if len(script.scenes) != len(timeline["scenes"]):
        raise QAInputError("script/timeline mismatch")
    if [scene.id for scene in script.scenes] != [str(row["id"]) for row in timeline["scenes"]]:
        raise QAInputError("script/timeline mismatch")
    for row in timeline["scenes"]:
        if "caption" not in row:
            # M26 supplies captions. The test fixtures must include them too.
            raise QAInputError("timeline scene caption missing")
    return {
        "video": video,
        "hf": hf,
        "html": html,
        "chunks": chunks,
        "timeline": timeline,
        "script": script,
        "scenes_dir": scenes_dir or work / "scenes",
        "fit_dir": fit_dir or work / "fit",
    }


def run_qa(
    work_dir: str | Path,
    *,
    transcriber: Callable[..., Any] | None = None,
    runner: Runner = _run,
    browser_factory: Callable[..., Any] | None = None,
    black_frozen_fn: Callable[..., Any] | None = None,
    video_path: str | Path | None = None,
    hf_dir: str | Path | None = None,
    chunks_path: str | Path | None = None,
    timeline_path: str | Path | None = None,
    script_path: str | Path | None = None,
    scenes_dir: str | Path | None = None,
    fit_dir: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Always run all gates independently, write report atomically, never publish on fail."""
    work = Path(work_dir)
    report_file = Path(report_path) if report_path else work / "qa_report.json"
    # A report from an earlier run must never vouch for this one.
    report_file.unlink(missing_ok=True)
    paths = _check_inputs(
        work,
        video_path=Path(video_path) if video_path else None,
        hf_dir=Path(hf_dir) if hf_dir else None,
        chunks_path=Path(chunks_path) if chunks_path else None,
        timeline_path=Path(timeline_path) if timeline_path else None,
        script_path=Path(script_path) if script_path else None,
        scenes_dir=Path(scenes_dir) if scenes_dir else None,
        fit_dir=Path(fit_dir) if fit_dir else None,
    )
    started = time.monotonic()
    timeline = paths["timeline"]
    total = float(timeline["total"])
    script = paths["script"]
    video, hf = paths["video"], paths["hf"]
    size = CONFIG[script.aspect]["size"]
    report_checks: dict[str, dict[str, Any]] = {}

    def execute(name: str, operation: Callable[[], dict[str, Any]]) -> None:
        try:
            result = operation()
            if not isinstance(result, dict) or not isinstance(result.get("pass"), bool):
                raise ValueError("check result lacks pass boolean")
            report_checks[name] = result
        except Exception as exc:
            report_checks[name] = _gate(False, error=_safe_error(exc))

    execute("hyperframes_check", lambda: hyperframes_check(hf, runner=runner))
    execute(
        "layout_playwright",
        lambda: layout_playwright(
            hf,
            timeline,
            paths["chunks"],
            script.aspect,
            browser_factory=browser_factory,
        ),
    )
    execute("file_probe", lambda: file_probe(video, total, size, runner=runner))
    execute("decode_clean", lambda: decode_clean(video, runner=runner))
    execute("blackdetect", lambda: blackdetect(video, runner=runner))
    execute("freezedetect", lambda: freezedetect(video, runner=runner))
    execute("silencedetect", lambda: silencedetect(video, total, runner=runner))
    execute("loudness_ebur128", lambda: loudness_ebur128(video, runner=runner))

    try:
        words = transcribe_once(
            video,
            script.language,
            transcriber=transcriber,
            runner=runner,
        )
        execute(
            "whisper_wer",
            lambda: whisper_wer(timeline, script.language, words, script.voice.lexicon),
        )
        execute("subtitle_sync", lambda: subtitle_sync(timeline, script.language, words))
    except Exception as exc:
        error = _safe_error(exc)
        report_checks["whisper_wer"] = _gate(False, error=error)
        report_checks["subtitle_sync"] = _gate(False, error=error)

    try:
        results = _results(timeline, paths["scenes_dir"])
    except Exception:
        results = {}
    execute(
        "clips_all_scenes",
        lambda: clips_all_scenes(
            timeline,
            results,
            paths["fit_dir"],
            paths["html"],
        ),
    )
    execute("clips_unique", lambda: clips_unique(timeline, results))
    execute("clips_resolution", lambda: clips_resolution(timeline, results, size))
    inspector = black_frozen_fn or checks.black_frozen
    execute(
        "clips_black_freeze",
        lambda: clips_black_freeze(
            timeline,
            paths["fit_dir"],
            inspector=inspector,
        ),
    )

    failed = [name for name in CHECK_NAMES if not report_checks[name]["pass"]]
    report = {
        "version": 1,
        "script_id": script_id(script),
        "video": {"sha256": _sha256(video), "size_bytes": video.stat().st_size},
        "pass": not failed,
        "failed": failed,
        "warnings": [
            {
                "scene_id": scene_id,
                "reason": warning,
            }
            for scene_id, result in sorted(results.items())
            if result.get("status") == "ok"
            for warning in (
                [result.get("degraded_reason", "weak_catalog_match")]
                if result.get("fallback")
                else []
            ) + ([result["quality_warning"]] if result.get("quality_warning") else [])
        ],
        "checks": report_checks,
        "time_s": round(time.monotonic() - started, 3),
    }
    _atomic(report_file, _json_bytes(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.qa")
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_qa(args.work, report_path=args.report)
    except QAInputError as exc:
        parser.exit(2, f"qa: {exc}\n")
    print(json.dumps({"pass": report["pass"], "failed": report["failed"]}, ensure_ascii=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
