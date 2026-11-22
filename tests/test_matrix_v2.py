"""M24 source matrix must never invent live evidence."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "render_matrix_v2", ROOT / "scripts/render_matrix_v2.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sources():
    return json.loads((ROOT / "data/sources.json").read_text())


def test_declarative_matrix_has_all_sources_four_languages_and_no_live_claim():
    content = module.render(sources())
    assert (
        len([line for line in content.splitlines() if line.startswith("| ")]) == len(sources()) + 1
    )
    assert content.count("pendiente del smoke en main") > len(sources())
    assert "Bilibili" not in content or "bilibili" in content
    assert "SUPPORTED_SEARCH_ONLY" in content
    assert "live success" not in content.lower()
    assert (ROOT / "docs/MATRIZ_FUENTES_V2.md").read_text() == content


def test_real_smoke_only_populates_its_specific_provider_language():
    smoke = {
        "mode": "live_network_probes",
        "timestamp_utc": "2026-11-21T20:00:00Z",
        "provider_languages": [
            {"provider": "bilibili", "language": "zh", "status": "RATE_LIMITED", "count": 0}
        ],
    }
    text = module.render(sources(), smoke)
    row = next(line for line in text.splitlines() if line.startswith("| bilibili |"))
    assert "RATE_LIMITED (n=0)" in row
    assert "pendiente del smoke en main" in row
    with pytest.raises(ValueError):
        module.render(sources(), {**smoke, "mode": "offline_fixture"})
    with pytest.raises(ValueError):
        module.render(sources(), {**smoke, "timestamp_utc": None})
