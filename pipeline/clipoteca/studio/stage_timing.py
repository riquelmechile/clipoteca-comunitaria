"""Best-effort numeric timing receipts for render jobs, without private paths."""

from __future__ import annotations

import os
from pathlib import Path


def write_timings(path: Path, label: str, values: dict[str, float]) -> None:
    from .plan import _atomic_json

    values = {key: round(value, 3) for key, value in values.items()}
    try:
        _atomic_json(path, values)
    except OSError:
        return
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        try:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write(
                    "\n**"
                    + label
                    + " (s):** "
                    + ", ".join(f"{key}={value:g}" for key, value in values.items())
                    + "\n"
                )
        except OSError:
            pass
