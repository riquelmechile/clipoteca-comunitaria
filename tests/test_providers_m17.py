"""M17 provider adapters are deterministic and network-free."""

import json
from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, SearchLimits
from clipoteca.providers.dzen import Dzen
from clipoteca.providers.ok_ru import OkRu
from clipoteca.providers import vk_video
from clipoteca.providers.vk_video import VKVideo

FIXTURES = Path(__file__).parent / "fixtures" / "m17"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def client(routes: dict[tuple[str, str], tuple[int, str, str]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body, content_type = routes[(request.method, request.url.path)]
        return httpx.Response(status, text=body, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ok_ru_search_and_mp4_resolution_from_html_fixtures():
    adapter = OkRu(
        client=client(
            {
                ("GET", "/video/search"): (200, fixture("ok_search.html"), "text/html"),
                ("GET", "/videoembed/1234567890"): (200, fixture("ok_embed.html"), "text/html"),
            }
        )
    )
    rows = await adapter.search("дождь", "ru", SearchLimits(max_candidates=3))
    assert [row.provider_id for row in rows] == ["1234567890"]
    media = await adapter.direct_media(rows[0])
    assert media and media.mime == "video/mp4" and media.url.endswith("movie.mp4")


@pytest.mark.asyncio
async def test_vk_anonymous_token_cache_search_and_hls(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", None)
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.vk.com":
            calls["token"] += 1
            return httpx.Response(200, json=json.loads(fixture("vk_token.json")))
        if request.url.path.endswith("catalog.getVideoSearchWeb2"):
            return httpx.Response(200, json=json.loads(fixture("vk_search.json")))
        if request.url.path.endswith("video.getByIds"):
            return httpx.Response(200, json=json.loads(fixture("vk_video.json")))
        raise AssertionError(request.url)

    adapter = VKVideo(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = await adapter.search("rain", "en", SearchLimits())
    media = await adapter.direct_media(rows[0])
    assert calls["token"] == 1
    assert rows[0].provider_id == "-7_42"
    assert media and media.mime == "application/vnd.apple.mpegurl"
    assert ".vkuser.net" in media.allowed_hosts


@pytest.mark.asyncio
async def test_dzen_search_and_hls_resolution_with_cookie_jar():
    adapter = Dzen(
        client=client(
            {
                ("GET", "/api/web/v1/zen-search"): (
                    200,
                    fixture("dzen_search.json"),
                    "application/json",
                ),
                ("GET", "/video/watch/dzen-video-42"): (
                    200,
                    fixture("dzen_watch.html"),
                    "text/html",
                ),
            }
        )
    )
    rows = await adapter.search("дождь", "ru", SearchLimits())
    media = await adapter.direct_media(rows[0])
    assert media and media.url.endswith("master.m3u8")
    assert media.allowed_hosts == (
        "dzen.ru",
        ".dzen.ru",
        "yandex.net",
        ".yandex.net",
        "yandex.ru",
        ".yandex.ru",
        "strm.yandex.ru",
        ".strm.yandex.ru",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,path",
    [
        (OkRu, "/video/search"),
        (Dzen, "/api/web/v1/zen-search"),
    ],
)
async def test_redirects_are_not_followed(adapter, path):
    instance = adapter(client=client({("GET", path): (302, "", "text/plain")}))
    with pytest.raises(ProviderError):
        await instance.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_provider_cdn_allowlist_rejects_lookalike():
    candidate = Candidate(
        "ok_ru",
        "1234567890",
        "rain",
        "en",
        "https://ok.ru/video/1234567890",
        "Rain",
        "en",
    )
    document = (
        "<div data-options='{"
        + '"videos":[{"url":"https://okcdn.ru.attacker.example/movie.mp4"}]'
        + "}'></div>"
    )
    adapter = OkRu(client=client({("GET", "/videoembed/1234567890"): (200, document, "text/html")}))
    assert await adapter.direct_media(candidate) is None
