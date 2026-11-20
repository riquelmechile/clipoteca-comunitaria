"""Hashes exactos y perceptuales reproducibles de clips."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

import imagehash
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
    estimated_frames = max(1, int(media.fps * media.duration_seconds))
    step = max(1, estimated_frames // frames)
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
            "-vsync",
            "vfr",
            "-frames:v",
            str(frames),
            str(pattern),
        ]
        subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=True
        )
        files = sorted(Path(directory).glob("frame-*.png"))
        if not files:
            raise RuntimeError("no frames available for perceptual hash")
        values: list[str] = []
        for frame in files:
            with Image.open(frame) as image:
                values.append(str(imagehash.phash(image.convert("RGB"))))
        return ":".join(values)


def perceptually_similar(left: str, right: str, *, max_distance: int = 6) -> bool:
    """Solo fusiona cuando todos los frames comparables son cercanos."""
    left_parts, right_parts = left.split(":"), right.split(":")
    if len(left_parts) != len(right_parts) or not left_parts:
        return False
    return all(
        imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b) <= max_distance
        for a, b in zip(left_parts, right_parts, strict=True)
    )
