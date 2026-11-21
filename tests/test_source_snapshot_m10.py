"""M10 public source status snapshots never expose credential values."""

import base64
import json

import httpx

from clipoteca.source_snapshot import publish_snapshot, snapshot


def test_snapshot_is_booleans_only_and_sensitive_values_never_leak(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "super-sensitive-test-secret")
    rows = snapshot()
    assert len(rows) == 15
    pexels = next(row for row in rows if row["name"] == "pexels")
    assert pexels["credentials_present"] is True
    assert "missing_credential_names" not in pexels
    assert "super-sensitive-test-secret" not in json.dumps(rows)
    for row in rows:
        assert isinstance(row["credentials_present"], bool)


def test_publish_snapshot_uses_optimistic_github_contents_api(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "super-sensitive-test-secret")
    seen = []

    def handler(request):
        assert request.url.host == "api.github.com"
        if request.method == "GET":
            return httpx.Response(404)
        seen.append(request)
        payload = json.loads(request.content)
        data = base64.b64decode(payload["content"])
        assert payload["branch"] == "main"
        assert "super-sensitive-test-secret" not in data.decode()
        return httpx.Response(201, json={"commit": {"sha": "abc"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = publish_snapshot(
            "riquelmechile/clipoteca-comunitaria", "test-token", client=client
        )
    assert result == "published"
    assert len(seen) == 1
