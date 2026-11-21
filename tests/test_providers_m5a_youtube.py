"""YouTube v3 endpoint fixtures: four languages, metadata and truthful resolution."""

import httpx
import pytest

from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
)
from clipoteca.providers.youtube import YouTube, _duration_seconds


VIDEO = "aB3cD4eF5gH"


def client(payload, status=200):
    def handler(request):
        if status != 200:
            return httpx.Response(
                status, json={"error": "unavailable"}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_youtube_official_search_for_each_language(lang):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": {"kind": "youtube#video", "videoId": VIDEO},
                        "snippet": {"title": "Rain in city", "defaultLanguage": "en"},
                    }
                ]
            },
        )

    adapter = YouTube(
        api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    found = await adapter.search("lluvia", lang, SearchLimits())
    params = seen[0].params
    assert (params["type"], params["videoDuration"], params["relevanceLanguage"]) == (
        "video",
        "short",
        lang,
    )
    assert params["part"] == "id,snippet"
    assert found[0].source_url == "https://www.youtube.com/watch?v=" + VIDEO
    assert found[0].source_language == "en"
    assert found[0].language == lang
    status, media, reason = await adapter.resolve_media(found[0])
    assert (status, media, reason) == (
        SupportStatus.SUPPORTED_SEARCH_ONLY,
        None,
        "data_api_no_direct_media",
    )


@pytest.mark.asyncio
async def test_videos_list_metadata_duration_without_invented_dimensions():
    def handler(request):
        assert request.url.path == "/youtube/v3/videos"
        assert request.url.params["part"] == "snippet,contentDetails"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": VIDEO,
                        "contentDetails": {"duration": "PT2M14S"},
                        "snippet": {"title": "Rain"},
                    }
                ]
            },
        )

    adapter = YouTube(
        api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    from clipoteca.providers.base import Candidate

    candidate = Candidate(
        "youtube", VIDEO, "rain", "en", "https://www.youtube.com/watch?v=" + VIDEO, "Rain", "en"
    )
    info = await adapter.metadata(candidate)
    assert info["duration_seconds"] == 134
    assert info["width"] is None and info["height"] is None
    assert _duration_seconds("PT12.5S") == 12.5


@pytest.mark.asyncio
async def test_missing_key_requires_auth_without_network():
    adapter = YouTube(
        api_key="",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: pytest.fail("network should not run without credentials")
            )
        ),
    )
    with pytest.raises(AuthRequired):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_empty_response_does_not_fake_candidates():
    adapter = YouTube(api_key="fake", client=client({"items": []}))
    assert await adapter.search("rain", "en", SearchLimits()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,exc", [(429, RateLimited), (500, ProviderError)])
async def test_youtube_error_is_typed(status, exc):
    adapter = YouTube(api_key="fake", client=client({}, status=status))
    with pytest.raises(exc):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_youtube_timeout_is_typed():
    from clipoteca.providers.base import ProviderTimeout

    def fail(_request):
        raise httpx.ReadTimeout("timeout")

    adapter = YouTube(api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(ProviderTimeout):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_youtube_invalid_id_dropped():
    adapter = YouTube(
        api_key="fake",
        client=client(
            {
                "items": [
                    {
                        "id": {"videoId": "../../../"},
                        "snippet": {"title": "Broken"},
                    }
                ]
            }
        ),
    )
    assert await adapter.search("rain", "en", SearchLimits()) == []
