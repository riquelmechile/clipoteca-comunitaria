"""Encargo 9: Wikimedia User-Agent, safe download diagnostics, Issue edit retry."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from clipoteca import contributions, ingest as ingest_module
from clipoteca.catalog import Catalog
from clipoteca.config import USER_AGENT
from clipoteca.contributions import (
    CommunityContribution,
    parse_community_issue,
    send_feedback,
)
from clipoteca.ingest import IngestReport
from clipoteca.jobs import JobStore, new_job, request_id
from clipoteca.providers.base import SearchLimits
from clipoteca.providers.http import fetch_json
from clipoteca.security.download import DownloadError, download_media


def _issue(body_note: str = "Nota original") -> dict:
    return {
        "number": 26,
        "state": "open",
        "title": "[Aporte pendiente] Olas en la playa",
        "user": {"login": "contributor"},
        "body": (
            "### URL pública del vídeo\n\n"
            "https://upload.wikimedia.org/wikipedia/commons/Waves.webm\n\n"
            "### Notas de procedencia\n\n" + body_note
        ),
    }


@pytest.mark.asyncio
async def test_download_sends_identifiable_user_agent_for_every_redirect(tmp_path: Path):
    seen: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("user-agent", ""))
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/final.webm"})
        if request.headers.get("user-agent") != USER_AGENT:
            return httpx.Response(403, text="Please set a user-agent")
        return httpx.Response(
            200,
            content=b"\x00\x00\x00\x18ftypisom",
            headers={"content-type": "video/webm"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        path = await download_media(
            "https://upload.wikimedia.org/video.webm",
            tmp_path,
            client=client,
            resolver=lambda _: ["8.8.8.8"],
        )
    assert path.exists()
    assert seen == [USER_AGENT, USER_AGENT]


@pytest.mark.asyncio
async def test_provider_json_adds_user_agent_without_overwriting_auth():
    seen = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("user-agent"), request.headers.get("authorization")))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert (
            await fetch_json(
                client,
                "https://example.com/json",
                headers={"Authorization": "Bearer placeholder"},
            )
            == {}
        )
    assert seen == [(USER_AGENT, "Bearer placeholder")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("HTTP 403", "download_error:HTTP 403"),
        ("HTTP 429", "download_error:HTTP 429"),
        ("HTTP 403 https://secret.invalid/?token=secret", "download_error"),
    ],
)
async def test_download_failure_source_errors_are_bounded(
    tmp_path: Path, monkeypatch, diagnostic: str, expected: str
):
    class Writer:
        def read_catalog(self):
            return Catalog(), "dummy-sha"

    class Storage:
        def close(self):
            pass

    async def broken_download(_url, _directory):
        raise DownloadError(diagnostic)

    monkeypatch.setattr(ingest_module, "localize_candidate", lambda *_a, **_k: object())
    issue = parse_community_issue(_issue(), 26)
    report = await ingest_module.ingest(
        issue.title,
        [CommunityContribution(issue)],
        repository="test/repo",
        token="fake",
        job_root=tmp_path,
        writer=Writer(),
        storage=Storage(),
        downloader=broken_download,
        expand_search=False,
    )
    errors = JobStore(tmp_path).load(report.request_id)["source_errors"]
    assert list(errors.values()) == [expected]
    assert contributions._feedback_reason(report.errors) == expected
    assert "secret.invalid" not in str(errors)
    assert "token=secret" not in repr(report.errors)


def test_download_http_reason_in_public_comment_is_allowlisted_only():
    comments = []

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/comments"):
            comments.append(request.read().decode())
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        send_feedback(
            contributions.OWNER_REPOSITORY,
            "secret-token",
            26,
            "failed",
            reason="download_error:HTTP 403",
            client=client,
        )
        send_feedback(
            contributions.OWNER_REPOSITORY,
            "secret-token",
            26,
            "failed",
            reason="download_error:HTTP 403 https://signed.invalid/?token=secret",
            client=client,
        )
    assert "download_error:HTTP 403" in comments[0]
    assert "processing_failed" in comments[1]
    assert "signed.invalid" not in "".join(comments)
    assert "secret-token" not in "".join(comments)


@pytest.mark.asyncio
async def test_editing_issue_restarts_only_its_revision_budget(monkeypatch, tmp_path: Path):
    original = parse_community_issue(_issue(), 26)
    changed = parse_community_issue(_issue("Nota modificada"), 26)
    assert original.revision != changed.revision
    assert original.source_url == changed.source_url
    first = await CommunityContribution(original).search(original.title, "es", SearchLimits())
    second = await CommunityContribution(changed).search(changed.title, "es", SearchLimits())
    assert first[0].provider_id == second[0].provider_id

    horizons = []

    async def capture_ingest(_query, _adapters, **kwargs):
        horizons.append(kwargs["horizon"])
        return IngestReport("a" * 64, "dead_letter", (), ("retry_budget_exhausted",))

    monkeypatch.setattr(contributions, "ingest", capture_ingest)
    await contributions.run_community_issue(original, contributions.OWNER_REPOSITORY, "token")
    await contributions.run_community_issue(original, contributions.OWNER_REPOSITORY, "token")
    await contributions.run_community_issue(changed, contributions.OWNER_REPOSITORY, "token")
    assert horizons[0] == horizons[1] != horizons[2]

    old_id = request_id(original.title, {"max_duration": 20}, horizons[0])
    new_id = request_id(changed.title, {"max_duration": 20}, horizons[2])
    store = JobStore(tmp_path)
    previous = new_job(old_id)
    previous["status"] = "dead_letter"
    previous["attempt"] = 3
    store.save(previous)
    assert store.load(old_id)["status"] == "dead_letter"
    assert store.load(new_id) is None
    assert request_id("olas", {}, "v1") == request_id("olas", {}, "v1")
