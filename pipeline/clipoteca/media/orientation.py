"""Clasificacion de relacion de aspecto sin recortar ni deformar."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd

TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class Orientation:
    aspect_ratio: str
    orientation: str
    reduced_ratio: str


def classify_orientation(width: int, height: int) -> Orientation:
    """Clasifica dimensiones efectivas con la tolerancia contractual."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    ratio = width / height
    divisor = gcd(width, height)
    reduced = f"{width // divisor}:{height // divisor}"
    if abs(ratio - 9 / 16) < TOLERANCE:
        return Orientation("9:16", "vertical", reduced)
    if abs(ratio - 16 / 9) < TOLERANCE:
        return Orientation("16:9", "horizontal", reduced)
    if abs(ratio - 1) < TOLERANCE:
        return Orientation("1:1", "square", reduced)
    return Orientation("other", "other", reduced)
