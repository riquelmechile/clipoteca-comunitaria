"""Contract, reservation and explicit video window regressions."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from clipoteca.security.download import DownloadError
from clipoteca.studio import checks, explicit_media
from clipoteca.studio.explicit_media import ExplicitMediaError, window_bounds
from clipoteca.studio.plan import write_plan
from clipoteca.studio.script import ScriptError, parse_video_script, script_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_mixed_script_and_reserved_clip(tmp_path):
    data = json.loads((FIXTURES / "m28/guion.json").read_text())
    catalogue = FIXTURES / "m28/catalogo.json"
    original = script_id(parse_video_script(data))
    for scene, ref in zip(data["scenes"][:2], ("u77", "clip-100001"), strict=True):
        scene.pop("query")
        scene.pop("prompt")
        scene["media"] = {"ref": ref}
    script = parse_video_script(data)
    assert script_id(script) != original
    plan = write_plan(script, tmp_path, catalogue)
    assert plan["scenes"][0]["source_kind"] == "contribution"
    assert plan["scenes"][1]["source_kind"] == "catalog_pinned"
    assignments = json.loads((tmp_path / "assignments.json").read_text())["scenes"]
    assert assignments[script.scenes[0].id]["status"] == "contribution"
    assert assignments[script.scenes[1].id]["primary"]["clip_id"] == "clip-100001"
    for scene in script.scenes[2:]:
        row = assignments[scene.id]
        assert all(
            candidate["clip_id"] != "clip-100001"
            for candidate in [row["primary"], *row["backups"]]
            if candidate is not None
        )


@pytest.mark.parametrize("media", [{"ref": "../x"}, {"ref": "u77", "start": 4, "end": 3}])
def test_invalid_reference_or_window(media):
    data = json.loads((FIXTURES / "m25/valid_min.json").read_text())
    data["scenes"][0]["media"] = media
    with pytest.raises(ScriptError):
        parse_video_script(data)


def test_search_requires_fallback_queries():
    data = json.loads((FIXTURES / "m25/valid_min.json").read_text())
    scene = data["scenes"][0]
    scene["media"] = {"ref": "u77"}
    scene["on_error"] = "search"
    scene.pop("query")
    with pytest.raises(ScriptError):
        parse_video_script(data)


def test_explicit_window_and_short_video():
    assert window_bounds({"start": 1, "end": 5}, 7, 5) == (1, 5)
    assert window_bounds({}, 7, 5) == (0, 7)
    with pytest.raises(ExplicitMediaError, match="demasiado corto"):
        window_bounds({"start": 1, "end": 4}, 7, 5)
    with pytest.raises(ExplicitMediaError, match="fuera"):
        window_bounds({"end": 8}, 7, 5)


def _contribution_job():
    return {
        "id": "scene-webm",
        "source_kind": "contribution",
        "media": {"ref": "u7a"},
        "aspect": "16:9",
        "len_est": 2,
        "len_fetch": 2,
        "min_short_side": 720,
    }


def _mock_contribution(monkeypatch, source):
    monkeypatch.setattr(
        explicit_media,
        "registry_record",
        lambda ref: {
            "url": "https://example.invalid/video?token=secret",
            "title": "Test video",
            "author": "Test author",
            "source": "Test source",
        },
    )

    async def download(url, folder, **kwargs):
        return source

    monkeypatch.setattr(explicit_media, "download_media", download)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)
def test_contribution_webm_without_frame_count(tmp_path, monkeypatch):
    encoders = subprocess.check_output(["ffmpeg", "-hide_banner", "-encoders"], text=True)
    codec = "libvpx-vp9" if "libvpx-vp9" in encoders else "libx264"
    source = tmp_path / ("source.webm" if codec == "libvpx-vp9" else "source.mkv")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=15",
            "-t",
            "5",
            "-c:v",
            codec,
            "-threads",
            "2",
            str(source),
        ],
        check=True,
        timeout=60,
    )
    # The old MP4-specific probe must reject this real input.
    with pytest.raises(checks.DecodeError):
        checks.probe_video(source)
    _mock_contribution(monkeypatch, source)
    root = tmp_path / "scene"
    root.mkdir()
    result = explicit_media.select_explicit(root, _contribution_job(), {})
    assert result["status"] == "ok", result
    assert result["origin"] == "contribution"
    assert result["quality_warning"] == "aporte de baja resoluci\u00f3n escalado"
    assert result["fallback"] is False
    assert result["clip"]["media_ref"] == "u7a"
    assert checks.probe_video(root / "clip.mp4") == (1920, 1080, 2.0)
    manifest = json.loads((root / "contribution" / "imported.json").read_text())
    assert manifest["clips"][0]["imp_id"] == result["clip"]["clip_id"]
    assert json.loads((root / "result.json").read_text()) == result


def test_corrupt_contribution_has_safe_probe_failure(tmp_path, monkeypatch):
    source = tmp_path / "corrupt.webm"
    source.write_bytes(b"invalid media")
    _mock_contribution(monkeypatch, source)
    result = explicit_media.select_explicit(tmp_path, _contribution_job(), {})
    assert result["status"] == "fail"
    assert result["reason"] == "No se pudo leer el video del aporte."
    assert result["skip_import"] is True
    assert json.loads((tmp_path / "result.json").read_text()) == result
    assert not (tmp_path / "clip.mp4").exists()
    assert not (tmp_path / "contribution" / "imported.json").exists()


@pytest.mark.parametrize("message, expected", [("HTTP 403", "HTTP 403."), ("secret", "")])
def test_download_error_is_safe(tmp_path, monkeypatch, message, expected):
    _mock_contribution(monkeypatch, tmp_path / "unused")

    async def download(*args, **kwargs):
        raise DownloadError(message)

    monkeypatch.setattr(explicit_media, "download_media", download)
    result = explicit_media.select_explicit(tmp_path, _contribution_job(), {})
    assert result["status"] == "fail"
    assert result["reason"] == ("No se pudo descargar el video del aporte. " + expected).strip()


def test_registry_http_error_is_safe(tmp_path, monkeypatch):
    def registry(ref):
        response = httpx.Response(
            403, request=httpx.Request("GET", "https://example.invalid/?token=secret")
        )
        response.raise_for_status()

    monkeypatch.setattr(explicit_media, "registry_record", registry)
    result = explicit_media.select_explicit(tmp_path, _contribution_job(), {})
    assert result["status"] == "fail"
    assert result["reason"] == "Registro de aporte no accesible o invalido. HTTP 403."


def test_default_search_for_contribution_with_queries():
    data = json.loads((FIXTURES / "m25/valid_min.json").read_text())
    scene = data["scenes"][0]
    scene["media"] = {"ref": "u77"}
    assert parse_video_script(data).scenes[0].on_error == "search"
    scene["on_error"] = "fail"
    assert parse_video_script(data).scenes[0].on_error == "fail"
