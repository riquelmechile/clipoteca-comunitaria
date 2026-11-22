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
        guard = (
            "github.ref == 'refs/heads/main' && github.event_name == "
            "'workflow_dispatch' && inputs.runner == 'self-hosted'"
        )
        if name.startswith("smoke"):
            guard = "github.repository == 'riquelmechile/clipoteca-comunitaria' && " + guard
        assert job["if"] == guard, name
        assert job["runs-on"] == ["self-hosted", "clipoteca-home"]
        assert any("uv run --no-sync" in step.get("run", "") for step in job["steps"])
    assert "Require approval for all external contributors" in text
    for job in ("smoke-actions", "smoke-self-hosted"):
        assert smoke["jobs"][job]["if"].startswith(
            "github.repository == 'riquelmechile/clipoteca-comunitaria' && "
        )
    assert "FRAGMENTOS" in text
    for var in (
        "NASA_API_KEY",
        "YOUTUBE_API_KEY",
        "VK_ACCESS_TOKEN",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_USER_ID",
        "SNAPCHAT_ACCESS_TOKEN",
        "SNAPCHAT_PROFILE_ID",
        "PEERTUBE_ALLOWED_INSTANCES",
        "DOUYIN_ACCESS_TOKEN",
        "TIKTOK_ACCESS_TOKEN",
        "EUROPEANA_API_KEY",
        "COVERR_API_KEY",
        "NARA_API_KEY",
        "DPLA_API_KEY",
        "SMITHSONIAN_API_KEY",
    ):
        assert var in text
    assert "NO incrementa attempt" in text and "no_results" in text
    assert "Environment=HOME=/srv/clipoteca-runner" in text
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
    section = text.split("## 4. YAML pendiente M19")[1].split("## 5. YAML pendiente M22")[0]
    assert section.count("DOUYIN_COOKIE: ") == 4
    assert section.count("TIKTOK_COOKIE: ") == 4
    assert "BILIBILI_COOKIE:" not in section
    for name in (
        "VK_ACCESS_TOKEN",
        "YOUTUBE_API_KEY",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_USER_ID",
        "SNAPCHAT_ACCESS_TOKEN",
        "SNAPCHAT_PROFILE_ID",
        "NASA_API_KEY",
        "DEMO_KEY",
        "PEERTUBE_ALLOWED_INSTANCES",
        "CLIPOTECA_IMAGE_MODEL_REVISION",
        "CLIPOTECA_TEXT_MODEL_REVISION",
        "CLIPOTECA_GITHUB_REPO",
        "CLIPOTECA_WRITE_TOKEN",
        "CLIPOTECA_ISSUE_TOKEN",
        "CLIPOTECA_DISPATCH_TOKEN",
        "MCP_ALLOWED_HOSTS",
        "MCP_ALLOWED_ORIGINS",
        "CATALOG_URL",
        "CATALOG_JOBS_URL",
        "CATALOG_TTL_SECONDS",
    ):
        assert name in text, name
