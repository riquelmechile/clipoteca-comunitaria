"""GitHub remote job lifecycle persists across runner restarts and respects revisions."""

import base64
import json

import httpx
import pytest

from clipoteca.jobs import GitHubJobWriter, JobStore, new_job, request_id


def test_remote_job_reopen_and_optimistic_commits(tmp_path):
    record = None
    revision = 0
    methods = []

    def github(request):
        nonlocal record, revision
        methods.append(request.method)
        if request.method == "GET":
            if record is None:
                return httpx.Response(404, json={"message": "Not Found"})
            payload = base64.b64encode(json.dumps(record).encode()).decode()
            return httpx.Response(
                200,
                json={
                    "sha": f"v{revision}",
                    "encoding": "base64",
                    "content": "\n".join(payload[i : i + 60] for i in range(0, len(payload), 60)),
                },
            )
        payload = request.read()
        obj = json.loads(payload)
        if record is not None:
            assert obj["sha"] == f"v{revision}"
        else:
            assert "sha" not in obj
        record = json.loads(base64.b64decode(obj["content"]))
        revision += 1
        return httpx.Response(200, json={"commit": {"sha": f"c{revision}"}})

    client = httpx.Client(transport=httpx.MockTransport(github))
    remote = GitHubJobWriter("test/repo", "dummy", client=client)
    ident = request_id("lluvia", {"max_duration": 20}, "v1")
    first = JobStore(tmp_path / "one", remote=remote)
    job = new_job(ident)
    job["terms"] = {"es": "lluvia", "en": "rain", "zh": "下雨", "ru": "дождь"}
    first.save(job)
    assert record["status"] == "queued"
    second = JobStore(tmp_path / "two", remote=remote)
    reopened = second.load(ident)
    assert reopened == job
    reopened["status"] = "searching"
    second.save(reopened)
    assert revision == 2
    reopened["status"] = "published"
    second.save(reopened)
    assert revision == 3
    reopened["status"] = "failed"
    with pytest.raises(ValueError, match="terminal"):
        second.save(reopened)
    assert revision == 3


def test_remote_job_invalid_request_id_never_calls_network():
    remote = GitHubJobWriter(
        "test/repo",
        "dummy",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network used"))),
    )
    with pytest.raises(ValueError):
        remote.read("../../secrets")
