"""Image scenes use real raster decoding and MP4 generation."""

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from clipoteca.studio import checks, explicit_media, image_motion
from clipoteca.studio.fit import fit_all
from clipoteca.studio.repo_assets import ASSETS, asset_path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_asset_allowlist_matches_checkout():
    allowed = json.loads((ROOT / "worker/src/repo-assets.json").read_text())
    assert allowed == sorted(
        "asset:" + path.stem for path in ASSETS.glob("*.svg") if path.stem != "clipoteca-typing"
    )
    with pytest.raises(ValueError, match="fuera de lista"):
        asset_path("asset:clipoteca-typing")
    with pytest.raises(ValueError, match="fuera de lista"):
        asset_path("asset:unknown")
    with pytest.raises(ValueError):
        asset_path("asset:../clipoteca-pipeline")


def test_jpeg_exif_rotation(tmp_path):
    source = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (1200, 1000), "red").save(source, exif=exif)
    loaded = image_motion.load_photo(source)
    assert loaded.size == (1000, 1200)
    assert not loaded.getexif()
    assert not loaded.info


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_transparent_raster_and_small_image(tmp_path, format):
    source = tmp_path / "photo"
    Image.new("RGBA", (1000, 700), (200, 40, 10, 128)).save(source, format=format)
    loaded = image_motion.load_photo(source)
    assert loaded.getpixel((0, 0))[3] == 128
    with pytest.raises(image_motion.ImageMotionError, match="Resolucion insuficiente"):
        image_motion.prepare_canvas(
            Image.new("RGB", (100, 100)), tmp_path / "canvas.png", "16:9", "cover"
        )


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)
def test_image_scene_and_fit_real_mp4(tmp_path, monkeypatch):
    source = tmp_path / "photo.png"
    image = Image.new("RGB", (1280, 960), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 960, 80):
        for x in range(0, 1280, 80):
            if (x // 80 + y // 80) % 2 == 0:
                draw.rectangle((x, y, x + 79, y + 79), fill=(25, 80, 160))
    image.save(source)
    monkeypatch.setattr(
        explicit_media,
        "registry_record",
        lambda ref: {"kind": "image", "url": "https://example.invalid/photo.png"},
    )

    async def download(*args, **kwargs):
        return source

    monkeypatch.setattr(explicit_media, "download_image", download)
    root = tmp_path / "scenes" / "photo"
    root.mkdir(parents=True)
    job = {
        "id": "photo",
        "source_kind": "contribution",
        "media": {"ref": "u78", "motion": "pan_right", "framing": "contain"},
        "aspect": "16:9",
        "len_est": 2,
        "len_fetch": 2,
    }
    result = explicit_media.select_explicit(root, job, {})
    assert result["status"] == "ok", result
    assert result["origin"] == "contribution_image"
    assert not (root / "contribution" / "imported.json").exists()
    assert checks.black_frozen(root / "clip.mp4") == ([], [])
    clips = tmp_path / "clips"
    clips.mkdir()
    shutil.copyfile(root / "clip.mp4", clips / "photo.mp4")
    report = fit_all(
        {"scenes": [{"id": "photo", "len": 3.5}]},
        clips,
        tmp_path / "fit",
        scenes_dir=root.parent,
    )
    assert not report["failed"], report
    assert report["scenes"]["photo"]["mode"] == "image_motion"
    assert checks.probe_video(tmp_path / "fit/photo.mp4") == (1920, 1080, 3.5)
    assert checks.black_frozen(tmp_path / "fit/photo.mp4") == ([], [])


@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("rsvg-convert", "ffmpeg", "ffprobe")),
    reason="librsvg and ffmpeg required",
)
@pytest.mark.parametrize("asset", ["clipoteca-pipeline", "clipoteca-studio"])
def test_repo_svg_motion(tmp_path, asset):
    job = {
        "id": "diagram",
        "source_kind": "repo_asset",
        "media": {"ref": f"asset:{asset}"},
        "aspect": "16:9",
        "len_est": 2,
        "len_fetch": 2,
    }
    result = explicit_media.select_explicit(tmp_path, job, {})
    assert result["status"] == "ok", result
    assert result["origin"] == "repo_asset"
    assert checks.black_frozen(tmp_path / "clip.mp4") == ([], [])
    assert not (tmp_path / "contribution" / "imported.json").exists()
