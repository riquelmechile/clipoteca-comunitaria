"""Exact SHA256 and variance-aware perceptual video fingerprints."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from .probe import probe_media


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_hash(path: str | Path, *, frames: int = 5, timeout: float = 60) -> str:
    if frames < 1:
        raise ValueError("frames must be positive")
    media = probe_media(path, timeout=min(timeout, 30))
    frame_count = max(1, int(media.fps * media.duration_seconds))
    step = max(1, frame_count // frames)
    with tempfile.TemporaryDirectory(prefix="clipoteca-phash-") as directory:
        pattern = Path(directory) / "frame-%03d.png"
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select='not(mod(n,{step}))',scale=320:-2",
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(frames),
            str(pattern),
        ]
        subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=True)
        files = sorted(Path(directory).glob("frame-*.png"))
        if not files:
            raise RuntimeError("no frames available for perceptual hash")
        values = []
        for frame in files:
            with Image.open(frame) as image:
                grayscale = np.asarray(image.convert("L"), dtype=np.float32)
                variance = float(grayscale.var())
                values.append(f"{imagehash.phash(image.convert('RGB'))}~{variance:.2f}")
        return ":".join(values)


def perceptually_similar(left: str, right: str, *, max_distance: int = 6) -> bool:
    """Compare common sampled frames and reject low-variance or legacy fingerprints."""
    left_parts, right_parts = left.split(":"), right.split(":")
    common = min(len(left_parts), len(right_parts))
    if not common:
        return False
    for a, b in zip(left_parts[:common], right_parts[:common], strict=True):
        if "~" not in a or "~" not in b:
            return False
        hash_a, variance_a = a.rsplit("~", 1)
        hash_b, variance_b = b.rsplit("~", 1)
        try:
            if float(variance_a) < 30 or float(variance_b) < 30:
                return False
            if imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b) > max_distance:
                return False
        except (ValueError, TypeError):
            return False
    return True
