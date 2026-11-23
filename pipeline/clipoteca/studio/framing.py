"""M28 geometry, visual-salience focus and cover-only FFmpeg framing."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def target_size(aspect: str) -> tuple[int, int]:
    """Return canonical pixels for the three schema aspect ratios."""
    targets = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
    if aspect not in targets:
        raise ValueError("aspect: unsupported")
    return targets[aspect]


def resolution_ok(
    sw: int,
    sh: int,
    width: int,
    height: int,
    min_short_side: int = 720,
) -> bool:
    if min(sw, sh, width, height) <= 0:
        return False
    if min(sw, sh) >= min_short_side:
        return True
    source_orientation = (sw > sh) - (sw < sh)
    target_orientation = (width > height) - (width < height)
    return source_orientation == target_orientation and max(sw, sh) >= 0.53 * max(width, height)


def saliency_focus(image: Any) -> tuple[float, float]:
    """Spectral-residual salience center of mass, safely falling back to center."""
    import cv2

    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    resized = cv2.resize(gray, (128, 128)).astype(np.float32)
    spectrum = np.fft.fft2(resized)
    log_amplitude = np.log(np.abs(spectrum) + 1e-6)
    phase = np.angle(spectrum)
    residual = log_amplitude - cv2.blur(log_amplitude, (3, 3))
    salience = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    salience = cv2.GaussianBlur(salience.astype(np.float32), (9, 9), 2.5)
    maximum = float(np.max(salience))
    if not math.isfinite(maximum) or maximum <= 1e-10:
        return (0.5, 0.5)
    salience /= maximum
    weights = salience * (salience > 0.3)
    mass = float(np.sum(weights))
    if not math.isfinite(mass) or mass <= 1e-10:
        return (0.5, 0.5)
    ys, xs = np.mgrid[0:128, 0:128]
    x = float(np.sum(xs * weights) / mass / 128)
    y = float(np.sum(ys * weights) / mass / 128)
    if not math.isfinite(x) or not math.isfinite(y):
        return (0.5, 0.5)
    return (float(np.clip(x, 0, 1)), float(np.clip(y, 0, 1)))


def cover_crop(
    sw: int,
    sh: int,
    width: int,
    height: int,
    focus: tuple[float, float] = (0.5, 0.5),
) -> tuple[float, float, float, float]:
    """Normalized (fx,fy,fw,fh) crop guaranteed inside the source frame."""
    if min(sw, sh, width, height) <= 0:
        raise ValueError("crop: invalid dimensions")
    sar = sw / sh
    tar = width / height
    if sar > tar:
        fw, fh = tar / sar, 1.0
    else:
        fw, fh = 1.0, sar / tar
    coords = []
    for coordinate in focus:
        try:
            value = float(coordinate)
        except (ValueError, TypeError):
            value = 0.5
        coords.append(value if math.isfinite(value) else 0.5)
    fx = float(np.clip(coords[0] - fw / 2, 0, 1 - fw))
    fy = float(np.clip(coords[1] - fh / 2, 0, 1 - fh))
    return fx, fy, fw, fh


def clip_positions(
    frames: list[np.ndarray],
    prompt_embedding: np.ndarray | None,
    scorer: Any,
    crop_width: float,
    current_x: float,
) -> float:
    """Compare nine horizontal cover positions, overriding only for +0.004 CLIP."""
    if prompt_embedding is None or scorer is None or crop_width >= 0.95 or not frames:
        return current_x
    selected = frames[:3]
    positions = np.linspace(0.0, max(0.0, 1 - crop_width), 9)
    clips: list[np.ndarray] = []
    for position in positions:
        for frame in selected:
            width = frame.shape[1]
            left = min(width - 1, max(0, round(float(position) * width)))
            right = min(width, max(left + 1, round((float(position) + crop_width) * width)))
            clips.append(frame[:, left:right])
    embeddings = np.asarray(scorer.image_emb(clips), dtype=np.float32)
    scores = (embeddings @ np.asarray(prompt_embedding)).reshape(9, len(selected)).mean(1)
    best = int(np.argmax(scores))
    previous = int(np.argmin(np.abs(positions - current_x)))
    if scores[best] - scores[previous] > 0.004:
        return float(positions[best])
    return current_x


def crop_filter(
    sw: int,
    sh: int,
    aspect: str,
    focus: tuple[float, float] = (0.5, 0.5),
    *,
    frames: list[np.ndarray] | None = None,
    prompt_embedding: np.ndarray | None = None,
    scorer: Any = None,
) -> tuple[str, tuple[float, float, float, float]]:
    """FFmpeg vf and normalized crop; never letterbox or use frozen padding."""
    width, height = target_size(aspect)
    fx, fy, fw, fh = cover_crop(sw, sh, width, height, focus)
    if sw / sh > width / height and fw < 0.95 and frames:
        fx = clip_positions(frames, prompt_embedding, scorer, fw, fx)
    vf = (
        f"crop=iw*{fw:.6f}:ih*{fh:.6f}:iw*{fx:.6f}:ih*{fy:.6f},"
        f"scale={width}:{height}:flags=lanczos,setsar=1,fps=30,format=yuv420p"
    )
    return vf, (fx, fy, fw, fh)
