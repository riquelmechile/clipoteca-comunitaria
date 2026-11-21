"""Rutube real response shape and truthful HLS/search-only behavior."""

import httpx
import pytest

from clipoteca.providers.base import ProviderError, ProviderTimeout, RateLimited, SearchLimits
from clipoteca.providers.rutube import Rutube


VIDEO = "8411dde9e6521796b6805e5966ad4297"


def fake(responses):
    def handler(request):
        status, payload = responses.get(request.url.path, (404, {}))
        return httpx.Response(status, json=payload, headers={"Retry-After": "0"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["es", "en", "zh", "ru"])
async def test_rutube_search_real_json_shape_four_languages(lang):
    adapter = Rutube(
        client=fake(
            {
                "/api/search/video/": (
                    200,
                    {
                        "count": 1,
                        "has_next": False,
                        "results": [
                            {
                                "id": VIDEO,
                                "title": "Rain on a city",
                                "video_url": "https://rutube.ru/video/" + VIDEO + "/",
                                "duration": 402,
                                "is_hidden": False,
                                "is_paid": False,
                            }
                        ],
                    },
                )
            }
        )
    )
    videos = await adapter.search("lluvia", lang, SearchLimits())
    assert len(videos) == 1
    assert videos[0].source_language == "en"
    assert videos[0].language == lang
    assert videos[0].duration_seconds == 402


@pytest.mark.asyncio
async def test_rutube_options_real_hls_is_discovered_as_playlist():
    adapter = Rutube(
        client=fake(
            {
                "/api/search/video/": (200, {"results": [{"id": VIDEO, "title": "Rain"}]}),
                "/api/play/options/" + VIDEO + "/": (
                    200,
                    {"video_balancer": {"default": "https://bl.rutube.ru/route/test.m3u8"}},
                ),
                "/api/video/" + VIDEO + "/": (
                    200,
                    {"title": "Rain", "duration": 30, "description": "Thunderstorm"},
                ),
            }
        )
    )
    video = (await adapter.search("rain", "en", SearchLimits()))[0]
    assert (await adapter.metadata(video))["duration_seconds"] == 30
    media = await adapter.direct_media(video)
    assert media and media.mime == "application/vnd.apple.mpegurl"
    assert media.url == "https://bl.rutube.ru/route/test.m3u8"
    assert media.allowed_hosts == ("bl.rutube.ru",)
    assert media.referer == "https://rutube.ru/video/" + VIDEO + "/"


@pytest.mark.asyncio
async def test_rutube_explicit_mp4_variant_if_present():
    adapter = Rutube(
        client=fake(
            {
                "/api/search/video/": (200, {"results": [{"id": VIDEO, "title": "Rain"}]}),
                "/api/play/options/" + VIDEO + "/": (
                    200,
                    {"video_balancer": {"mp4": "https://video.rtbcdn.ru/small.mp4"}},
                ),
            }
        )
    )
    video = (await adapter.search("rain", "en", SearchLimits()))[0]
    media = await adapter.direct_media(video)
    assert media and media.url.endswith("small.mp4")


@pytest.mark.asyncio
async def test_rutube_empty_or_paid_results():
    adapter = Rutube(
        client=fake(
            {
                "/api/search/video/": (
                    200,
                    {"count": 1, "results": [{"id": VIDEO, "title": "Paid", "is_paid": True}]},
                )
            }
        )
    )
    assert await adapter.search("rain", "ru", SearchLimits()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("code,exc", [(429, RateLimited), (500, ProviderError)])
async def test_rutube_http_error_status(code, exc):
    adapter = Rutube(client=fake({"/api/search/video/": (code, {})}))
    with pytest.raises(exc):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_rutube_network_timeout_typed():
    def timeout(_request):
        raise httpx.ConnectTimeout("Rutube deadline")

    adapter = Rutube(client=httpx.AsyncClient(transport=httpx.MockTransport(timeout)))
    with pytest.raises(ProviderTimeout):
        await adapter.search("rain", "ru", SearchLimits())
