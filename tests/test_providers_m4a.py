from __future__ import annotations

import json

import httpx
import pytest
from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    RateLimited,
    SearchLimits,
)
from clipoteca.providers.pexels import Pexels
from clipoteca.providers.pixabay import Pixabay


def http_client(body: dict | str, status: int = 200, content_type: str = "application/json"):
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 200 and isinstance(body, dict):
            return httpx.Response(status, json=body, headers={"content-type": content_type})
        return httpx.Response(
            status,
            content=body if isinstance(body, str) else json.dumps(body),
            headers={"content-type": content_type},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_pexels_realistic_response_and_direct_resolution():
    payload = {
        "videos": [
            {
                "id": 401,
                "url": "https://www.pexels.com/video/rain-401/",
                "duration": 12,
                "width": 1920,
                "height": 1080,
                "video_files": [
                    {
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.com/401-hd.mp4",
                    },
                    {
                        "file_type": "video/mp4",
                        "width": 640,
                        "height": 360,
                        "link": "https://videos.pexels.com/401-small.mp4",
                    },
                ],
            }
        ],
    }
    adapter = Pexels(api_key="test-key", client=http_client(payload))
    candidates = await adapter.search("下雨", "zh", SearchLimits())
    assert candidates[0].provider_id == "401"
    assert candidates[0].term == "下雨"
    assert (await adapter.direct_media(candidates[0])).url.endswith("401-hd.mp4")


@pytest.mark.asyncio
async def test_pixabay_valid_and_unavailable_variant():
    payload = {
        "hits": [
            {
                "id": 48,
                "pageURL": "https://pixabay.com/videos/id-48/",
                "tags": "rain",
                "videos": {
                    "large": {
                        "url": "https://cdn.pixabay.com/rain.mp4",
                        "width": 1920,
                        "height": 1080,
                        "size": 123,
                    },
                    "small": {"url": "", "width": 0, "height": 0},
                },
            }
        ]
    }
    adapter = Pixabay(api_key="test", client=http_client(payload))
    found = await adapter.search("lluvia", "es", SearchLimits())
    assert found[0].origin_id == "pixabay:48"
    media = await adapter.direct_media(found[0])
    assert media and media.mime == "video/mp4" and media.size_bytes == 123


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls", [Pexels, Pixabay])
async def test_auth_and_empty(adapter_cls):
    missing = adapter_cls(api_key="", client=http_client({}))
    with pytest.raises(AuthRequired):
        await missing.search("rain", "en", SearchLimits())
    key = adapter_cls(api_key="key", client=http_client({"hits": [], "videos": []}))
    assert await key.search("rain", "en", SearchLimits()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls", [Pexels, Pixabay])
@pytest.mark.parametrize("status,exception", [(429, RateLimited), (500, ProviderError)])
async def test_http_failure_statuses(adapter_cls, status, exception):
    adapter = adapter_cls(api_key="key", client=http_client({}, status))
    with pytest.raises(exception):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_cls", [Pexels, Pixabay])
async def test_html_not_mistaken_for_media_or_json(adapter_cls):
    adapter = adapter_cls(
        api_key="key", client=http_client("<html></html>", content_type="text/html")
    )
    with pytest.raises(ProviderError, match="non-JSON"):
        await adapter.search("rain", "en", SearchLimits())
