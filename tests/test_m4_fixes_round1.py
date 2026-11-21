"""Regression for real GitHub Contents Base64 and NASA HTTP manifest URLs."""

import base64
import json

import httpx
import pytest

from clipoteca.catalog import GitHubCatalogWriter
from clipoteca.providers.base import SearchLimits
from clipoteca.providers.nasa import NASA


def test_github_contents_multiline_base64_is_accepted():
    encoded = base64.b64encode(json.dumps([]).encode()).decode()
    multiline = "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60))

    def github(_request):
        return httpx.Response(200, json={"sha": "s1", "content": multiline, "encoding": "base64"})

    writer = GitHubCatalogWriter("owner/repo", "fake", client=httpx.Client(transport=httpx.MockTransport(github)))
    result, sha = writer.read_catalog()
    assert result.clips == []
    assert sha == "s1"


@pytest.mark.asyncio
async def test_nasa_real_http_manifest_and_details_provenance():
    def nasa(request):
        if request.url.path == "/search":
            return httpx.Response(200, json={"collection": {"items": [{
                "href": "https://images-api.nasa.gov/asset/A B",
                "data": [{"nasa_id": "A B", "media_type": "video", "title": "Rain on Earth"}],
            }]}})
        return httpx.Response(200, json={"collection": {"items": [
            {"href": "http://images-assets.nasa.gov/test~orig.mp4"},
            {"href": "http://images-assets.nasa.gov/test~small.mp4"},
            {"href": "http://images-assets.nasa.gov/test~medium.mp4"},
            {"href": "http://example.org/unsafe~medium.mp4"},
        ]}})

    adapter = NASA(client=httpx.AsyncClient(transport=httpx.MockTransport(nasa)))
    found = await adapter.search("雨", "zh", SearchLimits())
    assert found[0].source_url == "https://images.nasa.gov/details/A%20B"
    ref = await adapter.direct_media(found[0])
    assert ref and ref.url == "https://images-assets.nasa.gov/test~medium.mp4"
