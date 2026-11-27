"""Deterministic M30 HyperFrames/GSAP composition and verified H.264/AAC render."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import unicodedata
import wave
from pathlib import Path
from typing import Any

from .script import VideoScript, parse_video_script

GSAP_VERSION = "3.15.1"
HYPERFRAMES_VERSION = "0.9.36"
GSAP_TARBALL_SHA = "c70a3bcf656015f32b4f6456e686034564c90d64280db535cfbd84de4497d988"
GSAP_FILE_SHA = "3d1bea374ab1b703cc5c4105358eec63c4465efdc573931dc116e34a0a190c1b"
GSAP_FILE_SIZE = 73357
# Single quotes only: the stack is written inside double-quoted style attributes.
FONT = "Nunito,'DejaVu Sans',system-ui,sans-serif"
SHADOW = "0 4px 0 rgba(0,0,0,0.45),0 0 26px rgba(0,0,0,0.55)"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
HYPERFRAMES_CONFIG = {
    "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
    "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
    "paths": {
        "blocks": "compositions",
        "components": "compositions/components",
        "assets": "assets",
    },
    "media": {"autoProxy": True},
}
CONFIG = {
    "9:16": {
        "size": (1080, 1920),
        "block": (110, 860, 300, "center"),
        "title": (104, 130),
        "sub": 50,
        "cap": (120, 840, 1500, 60),
        "safe": (108, 192, 972, 1728),
    },
    "16:9": {
        "size": (1920, 1080),
        "block": (120, 760, 230, "left"),
        "title": (92, 110),
        "sub": 44,
        "cap": (260, 1400, 890, 54),
        "safe": (96, 54, 1824, 1026),
    },
    "1:1": {
        "size": (1080, 1080),
        "block": (90, 900, 80, "center"),
        "title": (70, 84),
        "sub": 36,
        "cap": (90, 900, 900, 46),
        "safe": (54, 54, 1026, 1026),
    },
}


class ComposeError(ValueError):
    """Composition has missing/inconsistent material or render cannot be verified."""


def _num(value: Any, field: str, *, nonnegative: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ComposeError(f"{field}: expected finite numeric seconds")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise ComposeError(f"{field}: expected finite nonnegative seconds")
    return number


def _valid_id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or ".." in value:
        raise ComposeError("unsafe scene id")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _read_wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() != 2 or stream.getframerate() != 48000:
                raise ComposeError("mix.wav must be stereo 48000 Hz")
            if stream.getsampwidth() != 2:
                raise ComposeError("mix.wav must be PCM16")
            return stream.getnframes() / stream.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        raise ComposeError("mix.wav missing or invalid") from exc


def _validate(script: VideoScript, timeline: Any, clips_dir: Path, mix: Path):
    if script.aspect not in CONFIG:
        raise ComposeError("unsupported aspect")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
        raise ComposeError("timeline.scenes must be a list")
    total = _num(timeline.get("total"), "timeline.total")
    beat = _num(timeline.get("beat"), "timeline.beat")
    if total <= 0 or beat <= 0:
        raise ComposeError("timeline.total and beat must be positive")
    rows = timeline["scenes"]
    ids = [_valid_id(scene.id) for scene in script.scenes]
    if len(ids) != len(set(ids)):
        raise ComposeError("duplicate script scene id")
    if len(rows) != len(ids):
        raise ComposeError("script/timeline scene mismatch")
    clips: dict[str, Path] = {}
    expected_start = 0.0
    for index, (scene, row) in enumerate(zip(script.scenes, rows, strict=True)):
        if not isinstance(row, dict) or _valid_id(row.get("id")) != ids[index]:
            raise ComposeError(f"script/timeline scene mismatch at index {index}")
        start = _num(row.get("start"), f"timeline.scenes[{index}].start")
        length = _num(row.get("len"), f"timeline.scenes[{index}].len")
        voice = _num(row.get("vo"), f"timeline.scenes[{index}].vo")
        duration = _num(row.get("dur"), f"timeline.scenes[{index}].dur")
        if length <= 0 or abs(start - expected_start) > 0.001:
            raise ComposeError(f"timeline.scenes[{index}]: discontinuous scene")
        if voice < start - 0.001 or voice + duration > start + length + 0.03:
            raise ComposeError(f"timeline.scenes[{index}]: voice outside scene")
        words = row.get("words")
        if not isinstance(words, list) or (duration > 0 and not words):
            raise ComposeError(f"timeline.scenes[{index}].words: missing words for voice")
        for j, word in enumerate(words):
            if not isinstance(word, list) or len(word) != 3 or not isinstance(word[0], str):
                raise ComposeError(f"timeline.scenes[{index}].words[{j}]: invalid")
            if not word[0].strip():
                raise ComposeError(f"timeline.scenes[{index}].words[{j}]: empty")
            a, b = _num(word[1], "word.start"), _num(word[2], "word.end")
            if b < a or voice + b > start + length + 0.03:
                raise ComposeError(f"timeline.scenes[{index}].words[{j}]: out of scene")
        src = clips_dir / f"{ids[index]}.mp4"
        if src.is_symlink() or any(parent.is_symlink() for parent in src.parents):
            raise ComposeError(f"symlink clip forbidden for scene {ids[index]}")
        if not src.is_file() or not src.stat().st_size:
            raise ComposeError(f"missing clip for scene {ids[index]}")
        clips[ids[index]] = src
        expected_start += length
    if abs(expected_start - total) > 0.001:
        raise ComposeError("timeline.total differs from scene lengths")
    if abs(_read_wav_duration(mix) - total) > 0.02:
        raise ComposeError("mix.wav duration differs from timeline.total")
    return total, beat, rows, clips


def fit(text: str, base: int, width: int, k: float = 0.6) -> int:
    """Fit longest word into the text block with deterministic integer pixels."""
    longest = max((len(word) for word in text.split()), default=1)
    return max(1, int(min(base, width / (longest * k))))


def _seconds(value: float) -> str:
    return format(round(value, 6), ".6f").rstrip("0").rstrip(".") or "0"


def _attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _title_zone_height(layout: dict[str, Any]) -> int:
    # Reserve clearance for entry animation and the subtitle pill.
    return layout["cap"][2] - layout["block"][2] - 96


def _wrapped_lines(text: str, size: int, width: int) -> int:
    if not text:
        return 0
    lines = 1
    used = 0.0
    for word in text.split():
        advance = _caption_units([word]) * size
        if used and used + advance > width:
            lines += 1
            used = 0.0
        used += advance
    return lines


def _title_sizes(scene: Any, layout: dict[str, Any]) -> tuple[int, int]:
    width = layout["block"][1]
    base = layout["title"][1 if scene.type == "intro" else 0]
    title = fit(scene.title, base, width)
    sub = fit(scene.sub, layout["sub"], width, 0.55)
    available = _title_zone_height(layout)
    while (
        _wrapped_lines(scene.title, title, width) * title * 1.1
        + _wrapped_lines(scene.sub, sub, width) * sub * 1.2
        + sub * 0.6
        > available
        or any(_caption_units([word]) * title > width for word in scene.title.split())
        or any(_caption_units([word]) * sub > width for word in scene.sub.split())
    ):
        if title >= sub:
            title -= 1
        else:
            sub -= 1
    return title, sub


def _title(scene: Any, row: dict[str, Any], layout: dict[str, Any]) -> str:
    left, width, top, align = layout["block"]
    title_font, sub_font = _title_sizes(scene, layout)
    # Real spaces between the animated words: inline-block margins alone collapse them.
    word_spans = " ".join(
        f'<span class="tw" style="display:inline-block;margin:0 {int(title_font * 0.11)}px">'
        f"{html.escape(word, quote=True)}</span>"
        for word in scene.title.split()
    )
    sub = html.escape(scene.sub, quote=True)
    return (
        f'<div id="sc_{scene.id}" class="clip scene" data-start="{_seconds(row["start"])}" '
        f'data-duration="{_seconds(row["len"])}" data-track-index="3" '
        'style="position:absolute;inset:0;z-index:4">'
        '<div class="scrim" style="position:absolute;inset:0;'
        "background:linear-gradient(180deg,rgba(0,0,0,0.58) 0%,rgba(0,0,0,0.12) 34%,"
        'rgba(0,0,0,0) 50%,rgba(0,0,0,0.10) 66%,rgba(0,0,0,0.55) 100%)"></div>'
        f'<div class="tblock" style="position:absolute;left:{left}px;width:{width}px;'
        f'top:{top}px;height:{_title_zone_height(layout)}px;overflow:hidden;text-align:{align}">'
        f'<div class="title" style="font:900 {title_font}px/1.05 {FONT};color:#FFFFFF;'
        f'text-shadow:{SHADOW}"><span class="qa-text">{word_spans}</span></div>'
        f'<div class="sub qa-text" style="margin-top:{int(sub_font * 0.6)}px;'
        f'font:800 {sub_font}px/1.15 {FONT};color:#FFFFFF;text-shadow:{SHADOW}">'
        f"{sub}</div></div></div>"
    )


# DejaVu Sans Bold advance widths in em, measured at 1000 px, rounded up
# after a 10% allowance for bold fallback fonts. Keep layout deterministic
# without requiring a browser or optional studio dependencies at build time.
_CAPTION_ADVANCES = {
    "a": 0.75,
    "b": 0.79,
    "c": 0.66,
    "d": 0.79,
    "e": 0.75,
    "f": 0.48,
    "g": 0.79,
    "h": 0.79,
    "i": 0.38,
    "j": 0.38,
    "k": 0.74,
    "l": 0.38,
    "m": 1.15,
    "n": 0.79,
    "o": 0.76,
    "p": 0.79,
    "q": 0.79,
    "r": 0.55,
    "s": 0.66,
    "t": 0.53,
    "u": 0.79,
    "v": 0.72,
    "w": 1.02,
    "x": 0.71,
    "y": 0.72,
    "z": 0.65,
    "A": 0.86,
    "B": 0.84,
    "C": 0.81,
    "D": 0.92,
    "E": 0.76,
    "F": 0.76,
    "G": 0.91,
    "H": 0.93,
    "I": 0.41,
    "J": 0.41,
    "K": 0.86,
    "L": 0.71,
    "M": 1.10,
    "N": 0.93,
    "O": 0.94,
    "P": 0.81,
    "Q": 0.94,
    "R": 0.85,
    "S": 0.80,
    "T": 0.76,
    "U": 0.90,
    "V": 0.86,
    "W": 1.22,
    "X": 0.85,
    "Y": 0.80,
    "Z": 0.80,
    "0": 0.77,
    "1": 0.77,
    "2": 0.77,
    "3": 0.77,
    "4": 0.77,
    "5": 0.77,
    "6": 0.77,
    "7": 0.77,
    "8": 0.77,
    "9": 0.77,
}


def _caption_units(words: list[str]) -> float:
    advances = [
        sum(
            _CAPTION_ADVANCES.get(char, 1.2)
            for char in unicodedata.normalize("NFD", word)
            if not unicodedata.combining(char)
        )
        for word in words
    ]
    # Word margins, pill padding and the active word's 1.08 scale.
    return sum(advances) + 0.4 * len(words) + 0.74 + 0.08 * max(advances, default=0.0)


def _caption_chunks(
    rows: list[dict[str, Any]],
    total: float,
    width: int,
    cap_font: int,
) -> list[dict[str, Any]]:
    max_units = (width - 6) / cap_font
    groups: list[list[tuple[str, float, float]]] = []
    for row in rows:
        current: list[tuple[str, float, float]] = []
        for word, relative_start, relative_end in row["words"]:
            value = (word, float(row["vo"]) + relative_start, float(row["vo"]) + relative_end)
            if current and _caption_units([w[0] for w in current] + [word]) > max_units:
                groups.append(current)
                current = []
            current.append(value)
            if len(current) >= 4 or word[-1] in ".,:;!?":
                groups.append(current)
                current = []
        if current:
            groups.append(current)
    chunks = []
    for index, group in enumerate(groups):
        start = group[0][1]
        next_start = groups[index + 1][0][1] if index + 1 < len(groups) else group[-1][2] + 0.4
        end = min(next_start, group[-1][2] + 0.6, total)
        duration = round(max(min(0.3, next_start - start - 0.002), end - start), 3)
        if not math.isfinite(duration) or duration <= 0:
            raise ComposeError("caption timing is invalid")
        estimated_size = int((width - 6) / _caption_units([word for word, _, _ in group]))
        chunks.append(
            {
                "words": group,
                "start": start,
                "end": start + duration,
                "duration": duration,
                "font_size": max(30, min(cap_font, estimated_size)),
            }
        )
    return chunks


def _caption_html(chunks: list[dict[str, Any]], layout: dict[str, Any]) -> str:
    left, width, top, default_size = layout["cap"]
    segments = []
    for index, chunk in enumerate(chunks):
        size = chunk.get("font_size", default_size)
        words = "".join(
            f'<span id="w{index}_{i}" class="cw" '
            f'style="display:inline-block;margin:0 {int(size * 0.2)}px;'
            f'max-width:{width - int(size * 1.2) - 8}px;overflow-wrap:anywhere">'
            f"{html.escape(text, quote=True)}</span>"
            for i, (text, _, _) in enumerate(chunk["words"])
        )
        segments.append(
            f'<div id="cap{index}" class="clip cap qa-text" '
            f'data-start="{_seconds(chunk["start"])}" '
            f'data-duration="{_seconds(chunk["duration"])}" '
            'data-track-index="7" '
            f'style="position:absolute;left:{left}px;width:{width}px;top:{top}px;'
            'text-align:center">'
            f'<span class="pill" style="display:inline-block;max-width:{width}px;'
            f"padding:{int(size * 0.23)}px {int(size * 0.37)}px;"
            "border-radius:28px;background:rgba(20,16,32,0.82);"
            f'font:900 {size}px/1.2 {FONT};color:#fff">{words}</span></div>'
        )
    return "".join(segments)


def _timeline_js(
    script: VideoScript,
    rows: list[dict[str, Any]],
    total: float,
    beat: float,
    chunks: list[dict[str, Any]],
) -> str:
    js = [
        "window.__timelines = window.__timelines || {};",
        "const tl = gsap.timeline({ paused: true });",
    ]
    for index, (scene, row) in enumerate(zip(script.scenes, rows, strict=True)):
        start, length = _seconds(row["start"]), float(row["len"])
        js.append(
            f'tl.fromTo("#v_{scene.id}",{{scale:1.06}},'
            '{scale:1.0,duration:0.45,ease:"power3.out",immediateRender:false},'
            f"{start});"
        )
        js.append(
            f'tl.from("#sc_{scene.id} .tw",'
            '{scale:0,y:60,opacity:0,duration:0.35,ease:"back.out(2.2)",'
            f"stagger:{_seconds(beat / 2)}" + "}," + f"{_seconds(float(row['start']) + 0.05)});"
        )
        js.append(
            f'tl.from("#sc_{scene.id} .sub",'
            '{y:40,opacity:0,duration:0.35,ease:"power2.out"},'
            f"{_seconds(float(row['start']) + min(beat * 2, length * 0.3))});"
        )
        if index + 1 < len(rows):
            js.append(
                f'tl.to("#sc_{scene.id} .tblock",'
                '{y:"-=40",opacity:0,duration:0.2,ease:"power2.in"},'
                f"{_seconds(float(row['start']) + length - 0.22)});"
            )
    for row in rows[1:]:
        cut = _seconds(row["start"])
        js.append(
            'tl.fromTo("#flash",{opacity:0.45},'
            '{opacity:0,duration:0.22,ease:"power2.out",immediateRender:false},'
            f"{cut});"
        )
    for k, chunk in enumerate(chunks):
        js.append(
            f'tl.from("#cap{k} .pill",'
            '{scale:0.85,opacity:0,duration:0.12,ease:"back.out(2)"},'
            f"{_seconds(chunk['start'])});"
        )
        for j, (_, begin, end) in enumerate(chunk["words"]):
            js.append(f'tl.set("#w{k}_{j}",{{color:"#FFD23F",scale:1.08}},{_seconds(begin)});')
            js.append(
                f'tl.set("#w{k}_{j}",{{color:"#fff",scale:1}},'
                f"{_seconds(min(end, chunk['end'] - 0.01))});"
            )
    js += [
        f"tl.set({{}},{{}}, {_seconds(total)});",
        'window.__timelines["main"] = tl;',
        "tl.seek(0);",
    ]
    return "\n".join(js)


def _html(
    script: VideoScript,
    rows: list[dict[str, Any]],
    total: float,
    beat: float,
) -> tuple[bytes, list[list[Any]]]:
    layout = CONFIG[script.aspect]
    width, height = layout["size"]
    chunks = _caption_chunks(rows, total, layout["cap"][1], layout["cap"][3])
    elements = []
    for scene, row in zip(script.scenes, rows, strict=True):
        elements.append(
            f'<video id="v_{scene.id}" class="clip" data-start="{_seconds(row["start"])}" '
            f'data-duration="{_seconds(row["len"])}" data-track-index="2" muted playsinline '
            f'src="assets/clips/{scene.id}.mp4" style="position:absolute;left:0;top:0;'
            f'width:{width}px;height:{height}px;object-fit:cover;z-index:2"></video>'
        )
        elements.append(_title(scene, row, layout))
    elements.append(
        f'<div id="flashLayer" data-layout-allow-occlusion class="clip" data-start="0" '
        f'data-duration="{_seconds(total)}" data-track-index="9" '
        'style="position:absolute;inset:0;z-index:21;pointer-events:none">'
        '<div id="flash" style="position:absolute;inset:0;background:#FFF6E0;'
        'opacity:0"></div></div>'
    )
    elements.append(
        f'<audio id="mix" class="clip" data-start="0" data-duration="{_seconds(total)}" '
        'data-track-index="8" data-volume="1" src="assets/mix.wav"></audio>'
    )
    elements.append(
        f'<div id="caps" data-layout-allow-occlusion class="clip" data-start="0" '
        f'data-duration="{_seconds(total)}" data-track-index="7" '
        'data-track-kind="captions" style="position:absolute;inset:0;z-index:15">'
        f"{_caption_html(chunks, layout)}</div>"
    )
    markup = (
        '<!doctype html><html lang="es"><head><meta charset="UTF-8"/>'
        f'<meta name="viewport" content="width={width}, height={height}"/>'
        '<script src="assets/gsap.min.js"></script>'
        "<style>*{margin:0;padding:0;box-sizing:border-box}"
        f"html,body{{width:{width}px;height:{height}px;overflow:hidden;background:#000}}"
        f"#root{{position:relative;width:100%;height:100%;font-family:{FONT}}}"
        "</style></head><body>"
        f'<div id="root" data-composition-id="main" data-start="0" '
        f'data-duration="{_seconds(total)}" data-width="{width}" data-height="{height}" '
        'data-fps="30">'
        + "".join(elements)
        + "</div><script>\n"
        + _timeline_js(script, rows, total, beat, chunks)
        + "\n</script></body></html>\n"
    )
    if re.search(r"https?://", markup, flags=re.IGNORECASE):
        raise ComposeError("HTML contains external network URL")
    manifest = [
        [[word for word, _, _ in chunk["words"]], round(chunk["start"], 6)] for chunk in chunks
    ]
    return markup.encode("utf-8"), manifest


def ensure_gsap(cache_dir: str | Path) -> Path:
    """Install one verified GSAP file from a verified npm tarball; no tar extraction."""
    target = Path(cache_dir) / "gsap.min.js"
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != GSAP_FILE_SHA:
            raise ComposeError("cached GSAP SHA-256 mismatch")
        if target.stat().st_size != GSAP_FILE_SIZE:
            raise ComposeError("cached GSAP size mismatch")
        return target
    with tempfile.TemporaryDirectory(prefix="clipoteca-gsap-") as folder:
        tmp = Path(folder)
        try:
            process = subprocess.run(
                ["npm", "pack", f"gsap@{GSAP_VERSION}", "--silent"],
                cwd=tmp,
                timeout=120,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComposeError("GSAP npm pack unavailable") from exc
        if process.returncode:
            raise ComposeError("GSAP npm pack failed")
        archive = tmp / f"gsap-{GSAP_VERSION}.tgz"
        if (
            not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest() != GSAP_TARBALL_SHA
        ):
            raise ComposeError("GSAP tarball SHA-256 mismatch")
        try:
            with tarfile.open(archive, "r:gz") as tar:
                entry = tar.getmember("package/dist/gsap.min.js")
                if not entry.isfile() or entry.size != GSAP_FILE_SIZE:
                    raise ComposeError("GSAP archive member invalid")
                member = tar.extractfile(entry)
                if member is None:
                    raise ComposeError("GSAP file unavailable in archive")
                data = member.read(GSAP_FILE_SIZE + 1)
        except (tarfile.TarError, OSError, KeyError) as exc:
            raise ComposeError("GSAP archive invalid") from exc
        if len(data) != GSAP_FILE_SIZE or hashlib.sha256(data).hexdigest() != GSAP_FILE_SHA:
            raise ComposeError("GSAP file SHA-256 mismatch")
        _atomic(target, data)
    return target


def build_composition(
    script: VideoScript,
    timeline: dict[str, Any],
    clips_dir: str | Path,
    mix_path: str | Path,
    out_dir: str | Path,
    gsap_path: str | Path,
) -> dict[str, Any]:
    """Stage the complete HF tree off to the side; publish or roll back as a unit."""
    clips_path, mix, gsap = Path(clips_dir), Path(mix_path), Path(gsap_path)
    total, beat, rows, clips = _validate(script, timeline, clips_path, mix)
    if not gsap.is_file() or not gsap.stat().st_size:
        raise ComposeError("missing GSAP asset")
    if (
        gsap.stat().st_size != GSAP_FILE_SIZE
        or hashlib.sha256(gsap.read_bytes()).hexdigest() != GSAP_FILE_SHA
    ):
        raise ComposeError("GSAP SHA-256 or size mismatch")
    markup, chunks = _html(script, rows, total, beat)
    root = Path(out_dir)
    if root.is_symlink() or (root / "hf").is_symlink():
        raise ComposeError("unsafe composition destination")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hf-build-", dir=root.parent) as folder:
        staged = Path(folder)
        target = staged / "hf"
        _atomic(target / "index.html", markup)
        _atomic(target / "hyperframes.json", _json_bytes(HYPERFRAMES_CONFIG))
        _atomic(target / "meta.json", _json_bytes({"id": "clipoteca", "name": "clipoteca"}))
        _atomic(
            target / "package.json", _json_bytes({"name": "hf", "private": True, "type": "module"})
        )
        _atomic(target / "assets" / "gsap.min.js", gsap.read_bytes())
        _atomic(target / "assets" / "mix.wav", mix.read_bytes())
        for scene_id, path in clips.items():
            _atomic(target / "assets" / "clips" / f"{scene_id}.mp4", path.read_bytes())
        _atomic(staged / "chunks.json", _json_bytes(chunks))

        created_root = not root.exists()
        root.mkdir(parents=True, exist_ok=True)
        hf = root / "hf"
        previous = staged / "previous-hf"
        had_previous = hf.exists()
        if had_previous:
            os.replace(hf, previous)
        try:
            os.replace(target, hf)
            _atomic(root / "chunks.json", (staged / "chunks.json").read_bytes())
        except Exception:
            if hf.is_dir():
                shutil.rmtree(hf)
            else:
                hf.unlink(missing_ok=True)
            if had_previous:
                os.replace(previous, hf)
            elif created_root:
                try:
                    root.rmdir()
                except OSError:
                    pass
            raise

    width, height = CONFIG[script.aspect]["size"]
    return {
        "hf": str(root / "hf"),
        "scenes": list(clips),
        "total": total,
        "chunks": len(chunks),
        "width": width,
        "height": height,
        "fps": 30,
    }


def _probe_output(path: Path, total: float, width: int, height: int) -> dict[str, Any]:
    try:
        process = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            timeout=40,
            capture_output=True,
            text=True,
            check=False,
        )
        data = json.loads(process.stdout) if process.returncode == 0 else {}
        streams = data["streams"]
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        audio = next(stream for stream in streams if stream.get("codec_type") == "audio")
        fps_num, fps_den = (int(value) for value in video["r_frame_rate"].split("/"))
        fps = fps_num / fps_den
        duration = float(data["format"]["duration"])
        if (
            video.get("codec_name") != "h264"
            or video.get("profile") != "High"
            or video.get("pix_fmt") != "yuv420p"
            or int(video["width"]) != width
            or int(video["height"]) != height
            or abs(fps - 30) > 0.01
            or audio.get("codec_name") != "aac"
            or int(audio.get("sample_rate", 0)) != 48000
            or int(audio.get("channels", 0)) != 2
            or abs(duration - total) > 0.2
        ):
            raise ComposeError("render media verification failed")
        return {"duration_s": round(duration, 3), "width": width, "height": height, "fps": 30}
    except (OSError, ValueError, KeyError, TypeError, StopIteration, ZeroDivisionError) as exc:
        if isinstance(exc, ComposeError):
            raise
        raise ComposeError("render ffprobe verification failed") from exc


def _render_metadata(hf: Path) -> tuple[float, int, int]:
    try:
        markup = (hf / "index.html").read_text(encoding="utf-8")

        def find(field: str) -> float:
            found = re.search(rf'<div id="root"[^>]*\bdata-{field}="([\d.]+)"', markup)
            if not found:
                raise ComposeError("render index root attributes missing")
            return float(found.group(1))

        total, width, height, fps = (
            find(field) for field in ("duration", "width", "height", "fps")
        )
        if not math.isfinite(total) or total <= 0 or int(fps) != 30:
            raise ComposeError("render root duration or fps invalid")
        if int(width) <= 0 or int(height) <= 0:
            raise ComposeError("render root dimensions invalid")
        if not (hf / "assets" / "mix.wav").is_file():
            raise ComposeError("render mix.wav missing")
        return total, int(width), int(height)
    except OSError as exc:
        raise ComposeError("render index.html missing") from exc


def render_video(
    hf_dir: str | Path,
    out_path: str | Path,
    *,
    timeout: int = 1500,
) -> dict[str, Any]:
    """Render then mux in a private directory; publish only if ffprobe gates pass."""
    hf, destination = Path(hf_dir), Path(out_path)
    # A retry must never leave the previous successful receipt as current truth.
    destination.with_name("render.json").unlink(missing_ok=True)
    total, width, height = _render_metadata(hf)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ComposeError("render timeout must be a positive integer")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hf-render-", dir=destination.parent) as folder:
        temporary = Path(folder)
        raw = temporary / "render.mp4"
        final = temporary / "final.mp4"
        started = time.monotonic()
        try:
            process = subprocess.run(
                ["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", "render", "-o", str(raw)],
                cwd=hf,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComposeError("HyperFrames rendering unavailable or timed out") from exc
        render_s = time.monotonic() - started
        sanitized = (process.stdout + "\n" + process.stderr).replace(str(hf), "<project>")
        sanitized = sanitized.replace(str(temporary), "<temporary>")
        _atomic(destination.with_name("render.log"), sanitized.encode("utf-8")[-32000:])
        if process.returncode or not raw.is_file() or not raw.stat().st_size:
            last = " | ".join(sanitized.strip().splitlines()[-3:])[-500:]
            raise ComposeError("HyperFrames render failed: " + last)
        mux_started = time.monotonic()
        try:
            mux = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(raw),
                    "-i",
                    str(hf / "assets" / "mix.wav"),
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "slow",
                    "-crf",
                    "20",
                    # Quality stays CRF-driven; the ceiling keeps a one-minute video near 75 MB.
                    "-maxrate",
                    "10M",
                    "-bufsize",
                    "20M",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "high",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(final),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComposeError("FFmpeg mux unavailable or timed out") from exc
        if mux.returncode or not final.is_file() or not final.stat().st_size:
            raise ComposeError("FFmpeg mux failed")
        mux_s = time.monotonic() - mux_started
        probe_started = time.monotonic()
        measured = _probe_output(final, total, width, height)
        probe_s = time.monotonic() - probe_started
        gsap_hash = hashlib.sha256((hf / "assets" / "gsap.min.js").read_bytes()).hexdigest()
        report = {
            **measured,
            "size_bytes": final.stat().st_size,
            "render_s": round(render_s, 3),
            "mux_s": round(mux_s, 3),
            "hyperframes": HYPERFRAMES_VERSION,
            "gsap_sha256": gsap_hash,
        }
        os.replace(final, destination)
        _atomic(destination.with_name("render.json"), _json_bytes(report))
        from .stage_timing import write_timings

        write_timings(
            destination.with_name("stage-compose-render.json"),
            "Compose render",
            {
                "render": render_s,
                "mux": mux_s,
                "probe": probe_s,
                "total": time.monotonic() - started,
            },
        )
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.compose")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--script", type=Path, required=True)
    build.add_argument("--timeline", type=Path, required=True)
    build.add_argument("--clips", type=Path, required=True)
    build.add_argument("--mix", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--gsap", type=Path)
    render = commands.add_parser("render")
    render.add_argument("--hf", type=Path, required=True)
    render.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            from .stage_timing import write_timings

            started = time.monotonic()
            script = parse_video_script(json.loads(args.script.read_text(encoding="utf-8")))
            timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
            gsap = args.gsap or ensure_gsap(args.out / ".gsap-cache")
            setup_done = time.monotonic()
            result = build_composition(
                script,
                timeline,
                args.clips,
                args.mix,
                args.out,
                gsap,
            )
            finished = time.monotonic()
            write_timings(
                args.out / "stage-compose-build.json",
                "Compose build",
                {
                    "setup": setup_done - started,
                    "build": finished - setup_done,
                    "total": finished - started,
                },
            )
        else:
            result = render_video(args.hf, args.out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"compose: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
