"""M24: operator YAML is complete and remains documentation-only."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def blocks(document: str) -> list[str]:
    marker = chr(96) * 3
    return re.findall(
        re.escape(marker) + r"yaml\n(.*?)\n" + re.escape(marker), document, flags=re.S
    )


def test_runner_templates_include_exact_safety_guard_and_inputs():
    text = (ROOT / "docs/RUNNER_PROPIO.md").read_text()
    snippets = blocks(text)
    assert len(snippets) == 2
    ingest = yaml.load(snippets[0], Loader=yaml.BaseLoader)
    smoke = yaml.load(snippets[1], Loader=yaml.BaseLoader)
    inputs = ingest["on"]["workflow_dispatch"]["inputs"]
    assert {"query", "request_id", "filters_json", "providers", "max_results", "runner"} <= set(
        inputs
    )
    assert smoke["on"]["workflow_dispatch"]["inputs"]["runner"]["default"] == "actions"
    for name, job in [
        ("ingest-self-hosted", ingest["jobs"]["ingest-self-hosted"]),
        ("smoke-self-hosted", smoke["jobs"]["smoke-self-hosted"]),
    ]:
        assert job["if"] == (
            "github.ref == 'refs/heads/main' && github.event_name == "
            "'workflow_dispatch' && inputs.runner == 'self-hosted'"
        ), name
        assert job["runs-on"] == ["self-hosted", "clipoteca-home"]
        assert any("uv run --no-sync" in step.get("run", "") for step in job["steps"])
    assert "Require approval for all outside collaborators" in text
    assert "NoNewPrivileges" in text and "ProtectHome" in text
    assert "PrivateTmp" in text and "ProtectSystem" in text
    assert "--ephemeral" in text
    assert "Ollama" in text
    assert "fork" in text


def test_operator_guide_collects_all_pending_workflow_sections():
    text = (ROOT / "docs/OPERACION_V2.md").read_text()
    for name in (
        "DOUYIN_COOKIE",
        "TIKTOK_COOKIE",
        "BILIBILI_COOKIE",
        "CLIPOTECA_M21_SIDECARS",
        "SEMANTIC_SEARCH",
        "CLIPOTECA_QUERY_DISPATCH_MODE",
        "QUERY_ENCODER_URL",
        "QUERY_ENCODER_HOSTS",
        "SEMANTIC_MAX_CLIPS",
        "SEMANTIC_MIN_COSINE",
        "EUROPEANA_API_KEY",
        "COVERR_API_KEY",
        "NARA_API_KEY",
        "DPLA_API_KEY",
        "SMITHSONIAN_API_KEY",
        "CLIPOTECA_RUNNER",
    ):
        assert name in text, name
    assert "clipoteca-query-embedding" in text
    assert "timeout-minutes" in text
    assert "RUNNER_PROPIO.md" in text
    assert "No declarar satisfaccion" in text
