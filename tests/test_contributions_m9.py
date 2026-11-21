"""M9 second gate: human-approved issues reuse the guarded ingest adapter."""

import httpx
import pytest

from clipoteca.contributions import (
    ApprovedContribution,
    parse_approved_issue,
    load_approved_issue,
)
from clipoteca.providers.base import SearchLimits


def fixture_issue(number=19, url="https://videos.example.org/videos/rain.mp4", approved=True):
    return {
        "number": number,
        "state": "open",
        "title": "[Aporte pendiente] Lluvia en la calle",
        "labels": [{"name": "clipoteca-approved"}] if approved else [],
        "body": (
            "### URL pública del vídeo\n\n"
            + url
            + "\n\n### Derechos de uso\n\n"
            + "Declaración de autorización del remitente; revisión humana requerida"
        ),
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
def test_approved_source_rejects_unsafe_or_indirect_urls(url):
    with pytest.raises(ValueError):
        parse_approved_issue(fixture_issue(url=url), 19)


def test_issue_without_human_approval_never_enters_pipeline():
    with pytest.raises(ValueError, match="clipoteca-approved"):
        parse_approved_issue(fixture_issue(approved=False), 19)


def test_issue_without_license_attestation_is_rejected():
    issue = fixture_issue()
    issue["body"] = issue["body"].replace("Declaración de autorización", "Sin declaración")
    with pytest.raises(ValueError, match="license"):
        parse_approved_issue(issue, 19)


@pytest.mark.asyncio
async def test_approved_adapter_yields_only_one_es_direct_candidate_no_download():
    approved = parse_approved_issue(fixture_issue(), 19)
    adapter = ApprovedContribution(approved)
    assert await adapter.search("rain", "en", SearchLimits()) == []
    items = await adapter.search("lluvia", "es", SearchLimits())
    assert len(items) == 1
    assert items[0].provider == "community_contribution"
    assert items[0].provider_id == "issue-19"
    assert (await adapter.direct_media(items[0])).mime == "video/mp4"


def test_github_read_only_load_does_not_download_media():
    def handler(request):
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        return httpx.Response(200, json=fixture_issue())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    issue = load_approved_issue(
        "riquelmechile/clipoteca-comunitaria", "test-token", 19, client=client
    )
    assert issue.number == 19
    assert issue.source_url.endswith("rain.mp4")
