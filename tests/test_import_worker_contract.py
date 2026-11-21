"""Worker dispatch filters must re-hash to the same Python job identifier.

Regression: real workflow 44857470094 accepted an import but exited before
writing a failed job due to a missing max_duration default in filters_json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipoteca import cli
from clipoteca.ingest import IngestReport
from clipoteca.jobs import JobStore, request_id


VECTORS = Path(__file__).resolve().parent / "fixtures" / "request_id_vectors.json"


@pytest.mark.parametrize(
    "vector",
    [v for v in json.loads(VECTORS.read_text(encoding="utf-8")) if "worker_import_filters" in v],
)
def test_worker_dispatch_filters_match_cli_import_path(monkeypatch, tmp_path, capsys, vector):
    assert request_id(vector["query"], vector["filters"], vector["horizon"]) == vector["request_id"]
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-test-token")
    monkeypatch.delenv("CLIPOTECA_PERSIST_JOBS", raising=False)
    monkeypatch.setattr(cli, "SOURCES", ())

    async def noop_ingest(query, adapters, **kw):
        assert kw["filters"] == vector["filters"]
        assert kw["horizon"] == vector["horizon"]
        assert query == vector["query"]
        assert not adapters
        return IngestReport(vector["request_id"], "no_results", (), ())

    monkeypatch.setattr(cli, "ingest", noop_ingest)
    code = cli.main(
        [
            "ingest",
            "--query",
            vector["query"],
            "--filters-json",
            json.dumps(vector["filters"]),
            "--request-id",
            vector["request_id"],
            "--job-root",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["request_id"] == vector["request_id"]


def test_request_id_mismatch_persists_failed_job_for_actions(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-test-token")
    monkeypatch.delenv("CLIPOTECA_PERSIST_JOBS", raising=False)
    incorrect_id = "f" * 64
    code = cli.main(
        [
            "ingest",
            "--query",
            "nieve",
            "--filters-json",
            '{"aspect_ratio":"16:9"}',
            "--request-id",
            incorrect_id,
            "--job-root",
            str(tmp_path),
        ]
    )
    assert code == 2
    record = JobStore(tmp_path).load(incorrect_id)
    assert record is not None
    assert record["status"] == "failed"
    assert record["source_errors"]["pipeline"] == "request_id_mismatch"
    assert "request_id mismatch" in capsys.readouterr().err


def test_request_id_mismatch_uses_remote_job_receipt_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-test-token")
    monkeypatch.setenv("CLIPOTECA_PERSIST_JOBS", "1")
    written = []

    class FakeJobWriter:
        def __init__(self, repository, token):
            assert repository == "example/repo"
            assert token == "dummy-test-token"

        def read(self, identifier):
            return None, None

        def save(self, job):
            written.append(dict(job))

    monkeypatch.setattr(cli, "GitHubJobWriter", FakeJobWriter)
    identifier = "e" * 64
    code = cli.main(
        [
            "ingest",
            "--query",
            "nieve",
            "--filters-json",
            '{"aspect_ratio":"16:9"}',
            "--request-id",
            identifier,
            "--job-root",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert len(written) == 1
    assert written[0]["request_id"] == identifier
    assert written[0]["status"] == "failed"
    assert written[0]["source_errors"]["pipeline"] == "request_id_mismatch"
