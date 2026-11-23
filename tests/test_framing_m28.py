"""M28 cover framing and salience tests, with CLIP sessions injected."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from clipoteca.studio.framing import (
    clip_positions,
    cover_crop,
    crop_filter,
    resolution_ok,
    saliency_focus,
    target_size,
)


def test_target_sizes_and_all_cover_orientations() -> None:
    assert target_size("9:16") == (1080, 1920)
    assert target_size("16:9") == (1920, 1080)
    assert target_size("1:1") == (1080, 1080)
    with pytest.raises(ValueError, match="aspect"):
        target_size("4:3")
    for source, dest in [
        ((1920, 1080), (1080, 1920)),
        ((1080, 1920), (1920, 1080)),
        ((1080, 1080), (1080, 1080)),
        ((800, 800), (1080, 1920)),
    ]:
        fx, fy, fw, fh = cover_crop(*source, *dest)
        assert all(0 <= value <= 1 for value in (fx, fy, fw, fh))
        assert fx + fw <= 1 + 1e-9 and fy + fh <= 1 + 1e-9
        assert (fw * source[0]) / (fh * source[1]) == pytest.approx(dest[0] / dest[1], rel=1e-6)
        vf, crop = crop_filter(*source, "9:16")
        assert vf.startswith("crop=")
        assert "scale=1080:1920:flags=lanczos" in vf
        assert "setsar=1,fps=30,format=yuv420p" in vf
        assert "pad=" not in vf
        assert crop == cover_crop(*source, 1080, 1920)


def test_focal_edges_nan_and_invalid_geometry() -> None:
    left = cover_crop(1920, 1080, 1080, 1920, (-100, 100))
    assert left[0] == 0 and left[1] == 0
    right = cover_crop(1920, 1080, 1080, 1920, (100, -100))
    assert right[0] == pytest.approx(1 - right[2])
    center = cover_crop(1920, 1080, 1080, 1920, (0.5, 0.5))
    bad = cover_crop(1920, 1080, 1080, 1920, (float("nan"), float("inf")))
    assert bad == center
    with pytest.raises(ValueError, match="dimensions"):
        cover_crop(0, 1080, 1080, 1920)


def test_resolution_uses_short_side_or_matching_orientation() -> None:
    assert resolution_ok(1280, 720, 1080, 1920)
    assert resolution_ok(1200, 700, 1920, 1080)
    assert not resolution_ok(1200, 700, 1080, 1920)
    assert resolution_ok(1080, 1080, 1080, 1080)
    assert not resolution_ok(600, 500, 1920, 1080)
    assert not resolution_ok(0, 1920, 1080, 1920)


def test_clip_positions_nine_way_chooses_only_significant_improvement() -> None:
    class FakeScorer:
        def __init__(self, flat: bool):
            self.flat = flat
            self.count = 0

        def image_emb(self, crops):
            self.count += 1
            assert len(crops) == 27
            return np.array(
                [[1.0 if self.flat else np.mean(crop[:, :, 0]) / 255] for crop in crops],
                dtype=np.float32,
            )

    gradient = np.tile(np.arange(255, dtype=np.uint8), (100, 2, 1))
    gradient = np.transpose(gradient, (0, 2, 1))
    gradient = np.repeat(gradient, 3, axis=2)
    frames = [gradient.copy() for _ in range(3)]
    prompt = np.array([1], dtype=np.float32)
    best = clip_positions(frames, prompt, FakeScorer(False), 0.3, 0.1)
    assert best == pytest.approx(0.7)
    assert clip_positions(frames, prompt, FakeScorer(True), 0.3, 0.1) == 0.1
    assert clip_positions(frames, prompt, None, 0.98, 0.1) == 0.1


def test_saliency_synthetic_bright_object_and_flat_fallback() -> None:
    pytest.importorskip("cv2")
    flat = Image.fromarray(np.zeros((256, 256, 3), dtype=np.uint8))
    x, y = saliency_focus(flat)
    assert 0 <= x <= 1 and 0 <= y <= 1
    pixels = np.zeros((256, 256, 3), dtype=np.uint8)
    pixels[40:100, 185:240] = 255
    cx, cy = saliency_focus(Image.fromarray(pixels))
    assert 0 <= cx <= 1 and 0 <= cy <= 1
    assert cx > 0.5


def test_package_lazy_exports_without_loading_media_dependencies() -> None:
    from clipoteca import studio

    assert studio.target_size("1:1") == (1080, 1080)
    assert callable(studio.clip_scene)
    assert callable(studio.check_global)
