"""Regression on provider source languages, real metadata and rate limits."""

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, ProviderError, SearchLimits, detect_source_language
from clipoteca.providers.http import fetch_json
from clipoteca.providers.pexels import Pexels
from clipoteca.providers.pixabay import Pixabay
from clipoteca.providers.wikimedia_commons import WikimediaCommons


def test_language_uses_source_not_search_term():
    assert detect_source_language("Rain") == "en"
    assert detect_source_language("Lluvia en la calle") == "es"
    assert detect_source_language("夜晚的雨") == "zh"
    assert detect_source_language("Дождь на улице") == "ru"


@pytest.mark.asyncio
async def test_retry_after_is_bounded_and_anonymous_403_not_auth():
    requests = 0
    def respond(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"Retry-After": "0"}) if requests < 3 else httpx.Response(200, json={})
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    assert await fetch_json(client, "https://example.com/json") == {}
    assert requests == 3
    forbidden = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, json={})))
    with pytest.raises(ProviderError):
        await fetch_json(forbidden, "https://example.com/json")


@pytest.mark.asyncio
async def test_pixabay_invalid_key_returns_auth_required():
    adapter = Pixabay(api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(400, text="Invalid API key", headers={"content-type": "text/plain"})
    )))
    with pytest.raises(AuthRequired):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_pixabay_zh_parameter_does_not_override_source_title_language():
    def respond(request):
        assert request.url.params["lang"] == "zh"
        return httpx.Response(200, json={"hits": [{"id": 1, "tags": "Rain", "pageURL": "https://pixabay.com/videos/id-1/"}]})
    adapter = Pixabay(api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    found = await adapter.search("下雨", "zh", SearchLimits())
    assert found[0].source_language == "en"


@pytest.mark.asyncio
async def test_pexels_missing_url_does_not_kill_search():
    adapter = Pexels(api_key="fake", client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"videos": [{"id": 12, "duration": 2}]})
    )))
    assert (await adapter.search("rain", "en", SearchLimits()))[0].source_title == "12"


@pytest.mark.asyncio
async def test_commons_provenance_is_file_page():
    def respond(request):
        assert request.url.params["prop"] == "imageinfo|info"
        assert request.url.params["iiprop"] == "url|size|mime|extmetadata"
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "ns": 6, "pageid": 1, "title": "File:Rain.mov",
            "imageinfo": [{"mime": "video/mp4", "url": "https://upload.wikimedia.org/video.mp4"}],
        }}}})
    adapter = WikimediaCommons(client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    found = await adapter.search("rain", "en", SearchLimits())
    assert found[0].source_url == "https://commons.wikimedia.org/wiki/File:Rain.mov"
