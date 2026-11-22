"""M24 source matrix must never invent live evidence."""

from __future__ import annotations

import asyncio
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
    # Fixed contract fixture: main's data/sources.json is rewritten by Actions
    # when credential presence changes and must not make CI flaky.
    return json.loads((ROOT / "tests/fixtures/m24/sources_fixed.json").read_text())


def test_declarative_matrix_has_all_sources_four_languages_and_no_live_claim():
    content = module.render(sources())
    rows = [line for line in content.splitlines() if line.startswith("| ")]
    assert len(sources()) == 23
    assert len(rows) == 24  # header + 23 declared providers
    assert all(len(row.split("|")) == 11 for row in rows)
    assert rows[0].startswith("| Proveedor | Estado | Search | Preview | Download |")
    assert all(language + " live" in rows[0] for language in ("es", "en", "zh", "ru"))
    assert content.count("pendiente del smoke en main") >= 23 * 4
    assert "SUPPORTED_SEARCH_ONLY" in content
    assert "live success" not in content.lower()


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


def test_live_zero_results_not_success_and_unknown_provider_rejected():
    smoke = {
        "mode": "live_network_probes",
        "timestamp_utc": "2026-11-21T20:00:00Z",
        "github": {"ref": "refs/heads/main", "sha": "a" * 40, "run_id": "1234"},
        "provider_languages": [
            {
                "provider": "bilibili",
                "language": "zh",
                "status": "SUPPORTED_SEARCH_ONLY",
                "count": 0,
            }
        ],
    }
    report = module.render(sources(), smoke)
    assert "sin resultados (n=0)" in report
    assert "refs/heads/main" in report and "a" * 40 in report
    assert "1234" in report
    with pytest.raises(ValueError, match="unknown provider"):
        module.render(
            sources(),
            {
                **smoke,
                "provider_languages": [
                    {"provider": "not_real", "language": "en", "status": "ok", "count": 1}
                ],
            },
        )


def test_smoke_report_carries_available_github_ref_sha_and_run_id(monkeypatch):
    smoke_spec = importlib.util.spec_from_file_location(
        "smoke_sources_m24", ROOT / "scripts/smoke_sources.py"
    )
    assert smoke_spec and smoke_spec.loader
    smoke_module = importlib.util.module_from_spec(smoke_spec)
    smoke_spec.loader.exec_module(smoke_module)
    monkeypatch.setattr(smoke_module, "SOURCES", ())
    monkeypatch.setattr(smoke_module, "github_usage", lambda: {})
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    data = asyncio.run(smoke_module.main_async())
    assert data["provider_languages"] == []
    assert data["github"] == {
        "ref": "refs/heads/main",
        "sha": "a" * 40,
        "run_id": "123456",
    }
