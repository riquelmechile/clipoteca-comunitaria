"""M9: un Issue público validado entra automáticamente al pipeline seguro."""

import hashlib
from pathlib import Path

import httpx
import pytest
from clipoteca import contributions
from clipoteca.ingest import IngestReport
from clipoteca.metadata import localize_candidate
from clipoteca.contributions import (
    CommunityContribution,
    parse_community_issue,
    load_community_issue,
    send_feedback,
)
from clipoteca.providers.base import SearchLimits


def issue(number=19, url="https://videos.example.org/videos/rain.mp4", author="contributor"):
    return {
        "number": number,
        "state": "open",
        "title": "[Aporte pendiente] Lluvia en la calle",
        "labels": [],
        "user": {"login": author},
        "body": "### URL pública del vídeo\n\n" + url + "\n\n### Notas de procedencia\n\nLluvia",
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example.org/video.mp4",
        "https://127.0.0.1/private.mp4",
        "https://localhost/video.mp4",
        "https://evil.example.org/watch/123",
        "https://user:password@public.example.org/video.mp4",
        "https://public.example.org/video.mp4?token=secret",
    ],
)
def test_source_rejects_unsafe_or_indirect_urls(url):
    with pytest.raises(ValueError):
        parse_community_issue(issue(url=url), 19)


def test_issue_without_approval_or_rights_is_accepted():
    row = parse_community_issue(issue(), 19)
    assert row.author == "contributor"
    assert row.issue_url == "https://github.com/riquelmechile/clipoteca-comunitaria/issues/19"


def test_malformed_issue_cannot_trigger_download():
    bad = issue()
    bad["title"] = "Arbitrary issue without template"
    with pytest.raises(ValueError, match="Aportar escena"):
        parse_community_issue(bad, 19)


@pytest.mark.asyncio
async def test_adapter_yields_one_direct_candidate_with_stable_provenance():
    row = parse_community_issue(issue(), 19)
    adapter = CommunityContribution(row)
    assert await adapter.search("rain", "en", SearchLimits()) == []
    items = await adapter.search("lluvia", "es", SearchLimits())
    assert len(items) == 1
    assert items[0].provider == "community"
    digest = hashlib.sha256(row.source_url.encode()).hexdigest()[:12]
    assert items[0].provider_id == f"issue-19-{digest}"
    assert items[0].metadata == {
        "issue_number": 19,
        "issue_author": "contributor",
        "issue_url": row.issue_url,
        "description": "Lluvia",
        "description_language": "es",
    }
    assert (await adapter.direct_media(items[0])).mime == "video/mp4"


def test_read_issue_is_data_not_direct_media_request():
    def handler(request):
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        return httpx.Response(200, json=issue())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = load_community_issue(
            "riquelmechile/clipoteca-comunitaria", "test-token", 19, client=client
        )
    assert row.source_url.endswith("rain.mp4")


def test_feedback_comments_labels_and_closes_only_after_publication():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(201 if request.method == "POST" else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_feedback(
            "riquelmechile/clipoteca-comunitaria", "test-token", 19, "published", client=client
        )
    assert [method for method, _ in seen] == ["POST", "POST", "PATCH"]
    seen.clear()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_feedback(
            "riquelmechile/clipoteca-comunitaria", "test-token", 19, "failed", client=client
        )
    assert [method for method, _ in seen] == ["POST", "POST"]


@pytest.mark.asyncio
async def test_community_uses_url_filename_and_notes_without_query_expansion(monkeypatch):
    data = issue(url="https://upload.wikimedia.org/wikipedia/commons/Waves_at_beach%2C_Sri_Lanka.webm")
    data["title"] = "[Aporte pendiente] Olas en la playa"
    data["body"] += "\n\n### Control\n\nAutomático"
    row = parse_community_issue(data, 19)
    assert row.notes == "Lluvia"

    async def fake_ingest(query, adapters, **kwargs):
        assert query == "Olas en la playa"
        assert kwargs["expand_search"] is False
        candidates = await adapters[0].search(query, "es", SearchLimits())
        assert len(candidates) == 1
        assert candidates[0].source_title == "Waves at beach, Sri Lanka"
        assert candidates[0].source_language == "en"
        assert candidates[0].metadata["description_language"] == "es"
        return IngestReport("a" * 64, "no_results", (), ())

    monkeypatch.setattr(contributions, "ingest", fake_ingest)
    report = await contributions.run_community_issue(row, "riquelmechile/clipoteca-comunitaria", "token")
    assert report.status == "no_results"


def test_community_translates_only_title_and_notes_not_search_terms():
    from clipoteca.providers.base import Candidate

    candidate = Candidate(
        "community", "issue-19", "Expansión de consulta que no se debe traducir", "es",
        "https://upload.wikimedia.org/waves.webm", "Waves on beach", "en",
        metadata={"description": "Olas en playa", "description_language": "es"},
    )
    translations = []
    expected = {
        ("Waves on beach", "en", "es"): "Olas en playa",
        ("Olas en playa", "es", "en"): "Waves on beach",
    }

    def translator(text, src, dst):
        translations.append((text, src, dst))
        return expected[text, src, dst]

    localized = localize_candidate(candidate, translator=translator, include_query_tags=False)
    assert localized.title == {"es": "Olas en playa", "en": "Waves on beach"}
    assert localized.description == {"es": "Olas en playa", "en": "Waves on beach"}
    assert translations == list(expected)


def test_all_translation_workflows_share_one_preparation_and_edited_retries():
    root = Path(__file__).resolve().parents[1]
    shared = ".github/actions/prepare-translations"
    action = (root / shared / "action.yml").read_text(encoding="utf-8")
    assert "clipoteca.translate.prepare_resources" in action
    assert "stanza_resources" in action and "argos" in action
    assert "actions/cache@" in action
    for name in ("ingest", "relocalize", "contribution"):
        workflow = (root / ".github/workflows" / f"{name}.yml").read_text(encoding="utf-8")
        assert workflow.count(f"uses: ./{shared}") == 1
        assert "clipoteca.translate.download_models" not in workflow
        assert "actions/cache@" not in workflow
    contribution = (root / ".github/workflows/contribution.yml").read_text(encoding="utf-8")
    assert "types: [opened, edited]" in contribution
    from clipoteca.jobs import RETRYABLE
    assert "failed" in RETRYABLE


def test_feedback_reports_safe_concrete_error_without_leaking_inputs():
    comments = []

    def handler(request):
        if request.url.path.endswith("/comments"):
            comments.append(request.read().decode("utf-8"))
        return httpx.Response(201 if request.method == "POST" else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_feedback(
            "riquelmechile/clipoteca-comunitaria", "secret-token", 26,
            "failed", reason="translation_failed:en", client=client,
        )
        send_feedback(
            "riquelmechile/clipoteca-comunitaria", "secret-token", 26,
            "failed", reason="https://bad.example/?token=secret-token", client=client,
        )
    assert "translation_failed:en" in comments[0]
    assert "editar el Issue" in comments[0]
    assert "processing_failed" in comments[1]
    assert "bad.example" not in "".join(comments)
    assert "secret-token" not in "".join(comments)
