"""M20 declarative runner partitions; no network, no workflow modification."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipoteca import cli
from clipoteca.runner import selected_providers


def config(tmp_path: Path, names: list[str]) -> Path:
    path = tmp_path / "runner-routing.json"
    path.write_text(json.dumps({"self_hosted_providers": names}), encoding="ascii")
    return path


def test_default_empty_partition_preserves_github_actions(tmp_path):
    path = config(tmp_path, [])
    assert "bilibili" in selected_providers("actions", set(), config_path=path)
    assert selected_providers("self-hosted", set(), config_path=path) == set()
    assert selected_providers("actions", {"bilibili", "pexels"}, config_path=path) == {
        "bilibili",
        "pexels",
    }


def test_per_provider_partition_disjoint_and_validated(tmp_path):
    path = config(tmp_path, ["bilibili"])
    assert selected_providers("self-hosted", set(), config_path=path) == {"bilibili"}
    assert selected_providers("actions", {"bilibili", "pexels"}, config_path=path) == {"pexels"}
    assert selected_providers("self-hosted", {"pexels"}, config_path=path) == set()
    with pytest.raises(ValueError, match="invalid"):
        selected_providers("random", set(), config_path=path)
    path.write_text('{"self_hosted_providers":["bilibili","bilibili"]}')
    with pytest.raises(ValueError, match="invalid"):
        selected_providers("self-hosted", set(), config_path=path)
    path.write_text('{"self_hosted_providers":["unknown"]}')
    with pytest.raises(ValueError, match="invalid"):
        selected_providers("actions", set(), config_path=path)


def test_cli_empty_self_hosted_runner_is_noop(tmp_path, monkeypatch, capsys):
    path = config(tmp_path, [])
    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/clipoteca")
    code = cli.main(
        [
            "ingest",
            "--query",
            "rain",
            "--providers",
            "bilibili",
            "--runner",
            "self-hosted",
            "--runner-config",
            str(path),
            "--job-root",
            str(tmp_path / "jobs"),
        ]
    )
    assert code == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "unavailable"
    assert receipt["deferred_sources"] == ["bilibili"]
    from clipoteca.jobs import JobStore

    job = JobStore(tmp_path / "jobs").load(receipt["request_id"])
    assert job["source_errors"]["bilibili"] == "deferred_to_actions"


def test_cli_selects_only_runner_allowed_adapters(tmp_path, monkeypatch):
    path = config(tmp_path, ["bilibili"])
    monkeypatch.setenv("GITHUB_TOKEN", "test-github-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/clipoteca")
    called = []

    class FakeBilibili:
        name = "bilibili"
        accepts_text_languages = ("es", "en", "zh", "ru")

        def __init__(self):
            self.client = None

    async def fake_ingest(query, adapters, **kwargs):
        called.append(
            (
                [adapter.name for adapter in adapters],
                kwargs["deferred_sources"],
                kwargs["deferred_reason"],
            )
        )
        return SimpleNamespace(
            request_id="a" * 64, status="no_results", published_ids=(), errors=()
        )

    monkeypatch.setattr(cli, "SOURCES", [SimpleNamespace(name="bilibili", adapter=FakeBilibili)])
    monkeypatch.setattr(cli, "ingest", fake_ingest)
    code = cli.main(
        [
            "ingest",
            "--query",
            "rain",
            "--providers",
            "bilibili,pexels",
            "--runner",
            "self-hosted",
            "--runner-config",
            str(path),
        ]
    )
    assert code == 0
    assert called == [(["bilibili"], ("pexels",), "deferred_to_actions")]
