"""M30 deterministic composition tests; external real render is explicitly opt-in."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import wave
from pathlib import Path

import pytest

from clipoteca.studio import compose
from clipoteca.studio.compose import (
    ComposeError,
    _probe_output,
    build_composition,
    ensure_gsap,
    render_video,
)
from clipoteca.studio.script import parse_video_script

FIXTURE = Path(__file__).parent / "fixtures/m30"
REAL_RENDER = os.environ.get("CLIPOTECA_M30_REAL_RENDER") == "1" and all(
    shutil.which(name) for name in ("npx", "ffmpeg", "ffprobe", "npm")
)


def _script():
    return parse_video_script(json.loads((FIXTURE / "guion.json").read_text(encoding="utf-8")))


def _timeline():
    return json.loads((FIXTURE / "timeline.json").read_text(encoding="utf-8"))


def _pcm_mix(path: Path, total: float = 6.0) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(bytes(round(total * 48000) * 4))


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    clips = tmp_path / "clips"
    clips.mkdir()
    for key in ("intro", "lago", "outro"):
        (clips / f"{key}.mp4").write_bytes(f"moving source {key}".encode())
    music = tmp_path / "mix.wav"
    _pcm_mix(music)
    gsap = tmp_path / "gsap.min.js"
    gsap.write_bytes(b"/* injected GSAP */")
    return clips, music, gsap


@pytest.mark.parametrize(
    ("aspect", "width", "height", "left", "font"),
    [
        ("9:16", 1080, 1920, 110, 60),
        ("16:9", 1920, 1080, 120, 54),
        ("1:1", 1080, 1080, 90, 46),
    ],
)
def test_build_all_aspects_exact_tracks_safe_text_and_determinism(
    tmp_path: Path,
    sources,
    aspect: str,
    width: int,
    height: int,
    left: int,
    font: int,
) -> None:
    clips, music, gsap = sources
    out = tmp_path / "build"
    result = build_composition(
        dataclasses.replace(_script(), aspect=aspect),
        _timeline(),
        clips,
        music,
        out,
        gsap,
    )
    assert (result["width"], result["height"], result["fps"]) == (width, height, 30)
    root = out / "hf"
    markup = (root / "index.html").read_text(encoding="utf-8")
    assert f'data-width="{width}" data-height="{height}" data-fps="30"' in markup
    assert f"left:{left}px" in markup
    assert f"font:900 {font}px/1.2" in markup
    assert markup.count('<video id="v_') == 3
    assert markup.count('class="clip scene"') == 3
    for index, key in enumerate(("intro", "lago", "outro")):
        assert f'id="v_{key}" class="clip" data-start="{index * 2}"' in markup
        assert f'id="sc_{key}" class="clip scene" data-start="{index * 2}"' in markup
        assert (root / "assets" / "clips" / f"{key}.mp4").read_bytes() == (
            clips / f"{key}.mp4"
        ).read_bytes()
    assert (root / "assets" / "mix.wav").read_bytes() == music.read_bytes()
    assert (root / "assets" / "gsap.min.js").read_bytes() == gsap.read_bytes()
    assert "&lt;azul&gt;" in markup
    assert "&amp;" in markup and "&quot;luz&quot;" in markup
    assert "<azul>" not in markup and 'agua & "luz"' not in markup
    assert "http://" not in markup and "https://" not in markup
    assert 'window.__timelines["main"] = tl;' in markup
    assert "const tl = gsap.timeline({ paused: true });" in markup
    assert "#FFD23F" in markup
    assert "clipoteca" not in markup.lower()
    assert "data-layout-allow-occlusion" in markup
    assert 'data-track-kind="captions"' in markup
    assert json.loads((root / "hyperframes.json").read_text()) == compose.HYPERFRAMES_CONFIG
    assert json.loads((root / "meta.json").read_text()) == {
        "id": "clipoteca",
        "name": "clipoteca",
    }
    assert json.loads((root / "package.json").read_text()) == {
        "name": "hf",
        "private": True,
        "type": "module",
    }
    chunks = json.loads((out / "chunks.json").read_text())
    assert chunks == [
        [["Cuidemos", "agua", "hoy"], 0.3],
        [["Cuidemos", "agua", "hoy"], 2.3],
        [["Cuidemos", "agua", "hoy"], 4.3],
    ]
    assert 'id="cap0" class="clip cap qa-text" data-start="0.3" data-duration="1.7"' in markup
    before = {
        name: (root / name).read_bytes()
        for name in (
            "index.html",
            "hyperframes.json",
            "meta.json",
            "package.json",
        )
    }
    before["chunks"] = (out / "chunks.json").read_bytes()
    build_composition(
        dataclasses.replace(_script(), aspect=aspect), _timeline(), clips, music, out, gsap
    )
    assert before == {
        **{
            name: (root / name).read_bytes()
            for name in (
                "index.html",
                "hyperframes.json",
                "meta.json",
                "package.json",
            )
        },
        "chunks": (out / "chunks.json").read_bytes(),
    }


def test_missing_clip_missing_words_and_mismatches_no_partial_files(
    tmp_path: Path,
    sources,
) -> None:
    clips, music, gsap = sources
    (clips / "lago.mp4").unlink()
    with pytest.raises(ComposeError, match="missing clip"):
        build_composition(_script(), _timeline(), clips, music, tmp_path / "a", gsap)
    assert not (tmp_path / "a").exists()
    (clips / "lago.mp4").write_bytes(b"clip")
    timeline = _timeline()
    timeline["scenes"][0]["words"] = []
    with pytest.raises(ComposeError, match="missing words"):
        build_composition(_script(), timeline, clips, music, tmp_path / "b", gsap)
    timeline = _timeline()
    timeline["scenes"][2]["id"] = "unexpected"
    with pytest.raises(ComposeError, match="scene mismatch"):
        build_composition(_script(), timeline, clips, music, tmp_path / "c", gsap)
    timeline = _timeline()
    timeline["scenes"].pop()
    with pytest.raises(ComposeError, match="scene mismatch"):
        build_composition(_script(), timeline, clips, music, tmp_path / "d", gsap)
    (clips / "outro.mp4").unlink()
    with pytest.raises(ComposeError, match="missing clip"):
        build_composition(_script(), _timeline(), clips, music, tmp_path / "e", gsap)
    assert not any((tmp_path / x).exists() for x in "abcde")


def test_mix_absent_and_bad_voice_timing(tmp_path: Path, sources) -> None:
    clips, music, gsap = sources
    music.unlink()
    with pytest.raises(ComposeError, match="mix.wav"):
        build_composition(_script(), _timeline(), clips, music, tmp_path / "out", gsap)
    _pcm_mix(music)
    timeline = _timeline()
    timeline["scenes"][1]["words"][0][2] = 5
    with pytest.raises(ComposeError, match="out of scene"):
        build_composition(_script(), timeline, clips, music, tmp_path / "out", gsap)
    timeline = _timeline()
    timeline["scenes"][1]["start"] = 10
    with pytest.raises(ComposeError, match="discontinuous"):
        build_composition(_script(), timeline, clips, music, tmp_path / "out", gsap)


def test_gsap_verified_tar_with_fake_npm_and_cache(tmp_path: Path, monkeypatch) -> None:
    fake_data = b"/* GSAP TEST IMPLEMENTATION */"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("package/dist/gsap.min.js")
        info.size = len(fake_data)
        archive.addfile(info, io.BytesIO(fake_data))
    archive_data = buffer.getvalue()
    source = tmp_path / "fixture.tar.gz"
    source.write_bytes(archive_data)
    monkeypatch.setattr(compose, "GSAP_FILE_SIZE", len(fake_data))
    monkeypatch.setattr(compose, "GSAP_FILE_SHA", hashlib.sha256(fake_data).hexdigest())
    monkeypatch.setattr(compose, "GSAP_TARBALL_SHA", hashlib.sha256(archive_data).hexdigest())
    tools = tmp_path / "bin"
    tools.mkdir()
    npm = tools / "npm"
    npm.write_text(f"#!/bin/sh\ncp '{source}' ./gsap-3.15.1.tgz\nprintf 'gsap-3.15.1.tgz\\n'\n")
    npm.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ.get('PATH', '')}")
    cache = tmp_path / "cache"
    assert ensure_gsap(cache).read_bytes() == fake_data
    npm.unlink()
    assert ensure_gsap(cache).read_bytes() == fake_data
    (cache / "gsap.min.js").write_bytes(b"tampered")
    with pytest.raises(ComposeError, match="SHA-256"):
        ensure_gsap(cache)
    (cache / "gsap.min.js").unlink()
    npm.write_text(f"#!/bin/sh\ncp '{source}' ./gsap-3.15.1.tgz\n")
    npm.chmod(0o755)
    monkeypatch.setattr(compose, "GSAP_TARBALL_SHA", "0" * 64)
    with pytest.raises(ComposeError, match="tarball SHA-256 mismatch"):
        ensure_gsap(cache)


def test_render_bad_npx_and_bad_ffprobe_leave_no_video(
    tmp_path: Path,
    sources,
    monkeypatch,
) -> None:
    clips, music, gsap = sources
    out = tmp_path / "build"
    build_composition(_script(), _timeline(), clips, music, out, gsap)
    tools = tmp_path / "bin"
    tools.mkdir()
    npx = tools / "npx"
    npx.write_text("#!/bin/sh\nprintf 'render failed\\n' >&2\nexit 9\n")
    npx.chmod(0o755)
    ffprobe = tools / "ffprobe"
    ffprobe.write_text("#!/bin/sh\nprintf '{}\\n'\n")
    ffprobe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}:{os.environ.get('PATH', '')}")
    with pytest.raises(ComposeError, match="HyperFrames render failed"):
        render_video(out / "hf", out / "video.mp4", timeout=15)
    assert not (out / "video.mp4").exists()
    assert "render failed" in (out / "render.log").read_text()
    with pytest.raises(ComposeError, match="ffprobe"):
        _probe_output(out / "missing.mp4", 6.0, 1080, 1920)


@pytest.mark.skipif(not REAL_RENDER, reason="real render opt-in requires npx, ffmpeg and npm")
def test_real_three_scene_render(tmp_path: Path) -> None:
    """Network-dependent, real browser: CI studio must opt in explicitly."""
    clips = tmp_path / "clips"
    clips.mkdir()
    for i, name in enumerate(("intro", "lago", "outro")):
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=1080x1920:rate=30,eq=brightness={i * 0.01}",
                "-t",
                "2",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(clips / f"{name}.mp4"),
            ],
            check=True,
            timeout=180,
            capture_output=True,
        )
    mix = tmp_path / "mix.wav"
    _pcm_mix(mix)
    gsap = ensure_gsap(tmp_path / "gsap-cache")
    root = tmp_path / "render"
    build_composition(_script(), _timeline(), clips, mix, root, gsap)
    report = render_video(root / "hf", root / "video.mp4", timeout=1500)
    assert report["width"] == 1080 and report["height"] == 1920
    assert report["fps"] == 30
    assert abs(report["duration_s"] - 6) <= 0.2
    assert report["gsap_sha256"] == compose.GSAP_FILE_SHA
    assert report["render_s"] > 0 and report["mux_s"] > 0
    assert json.loads((root / "render.json").read_text()) == report


def test_unsafe_scene_id_rejected_before_writing(tmp_path: Path, sources) -> None:
    clips, music, gsap = sources
    script = _script()
    first = dataclasses.replace(script.scenes[0], id="../bad")
    script = dataclasses.replace(script, scenes=(first, *script.scenes[1:]))
    with pytest.raises(ComposeError, match="unsafe scene id"):
        build_composition(script, _timeline(), clips, music, tmp_path / "out", gsap)
    assert not (tmp_path / "out").exists()


def test_cli_build_with_injected_gsap(tmp_path: Path, sources, capsys) -> None:
    clips, music, gsap = sources
    root = tmp_path / "built"
    assert (
        compose.main(
            [
                "build",
                "--script",
                str(FIXTURE / "guion.json"),
                "--timeline",
                str(FIXTURE / "timeline.json"),
                "--clips",
                str(clips),
                "--mix",
                str(music),
                "--out",
                str(root),
                "--gsap",
                str(gsap),
            ]
        )
        == 0
    )
    reply = json.loads(capsys.readouterr().out.strip())
    assert reply["scenes"] == ["intro", "lago", "outro"]
    assert reply["total"] == 6
    assert (root / "hf" / "index.html").exists()


def test_every_style_attribute_survives_html_parsing(tmp_path: Path, sources) -> None:
    """A double quote inside a style value would cut it short and drop font, colour and shadow."""
    from html.parser import HTMLParser

    clips, music, gsap = sources
    build_composition(_script(), _timeline(), clips, music, tmp_path / "build", gsap)
    markup = (tmp_path / "build" / "hf" / "index.html").read_text(encoding="utf-8")
    known = {
        "id",
        "class",
        "style",
        "src",
        "lang",
        "charset",
        "name",
        "content",
        "muted",
        "playsinline",
        "data-start",
        "data-duration",
        "data-track-index",
        "data-volume",
        "data-track-kind",
        "data-composition-id",
        "data-width",
        "data-height",
        "data-fps",
        "data-layout-allow-occlusion",
    }
    styles: dict[str, str] = {}

    class Collector(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            names = {name for name, _ in attrs}
            assert names <= known, f"<{tag}> has stray attributes: {sorted(names - known)}"
            values = dict(attrs)
            for css_class in (values.get("class") or "").split():
                styles.setdefault(css_class, values.get("style") or "")

    Collector().feed(markup)
    for css_class in ("title", "sub", "pill"):
        assert "DejaVu Sans" in styles[css_class], css_class
        assert "color:#" in styles[css_class], css_class
    assert "text-shadow:" in styles["title"] and "text-shadow:" in styles["sub"]
    assert '"' not in compose.FONT
