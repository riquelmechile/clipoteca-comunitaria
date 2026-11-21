"""M9: un Issue público validado entra automáticamente al pipeline seguro."""

import hashlib
import httpx
import pytest
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
