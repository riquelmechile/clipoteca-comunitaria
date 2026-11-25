"""Only checked-in SVGs, never a user-controlled file path."""

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[3] / "assets"


def asset_path(ref: str) -> Path:
    if not re.fullmatch(r"asset:[a-z0-9][a-z0-9-]{0,63}", ref):
        raise ValueError("Asset fuera de lista.")
    path = ASSETS / (ref[6:] + ".svg")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Asset fuera de lista.")
    return path
