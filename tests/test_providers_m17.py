"""M17 provider adapters are deterministic and network-free."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import (
    AuthRequired,
    Candidate,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
)
from clipoteca.providers.dzen import ALLOWED_HOSTS as DZEN_HOSTS
from clipoteca.providers.dzen import Dzen
from clipoteca.providers.ok_ru import ALLOWED_HOSTS as OK_HOSTS
from clipoteca.providers.ok_ru import OkRu
from clipoteca.providers import vk_video
from clipoteca.providers.vk_video import VKVideo
from clipoteca.security.download import DownloadError, download_media

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
    rows = await adapter.search("дождь", "ru", SearchLimits(max_pages=1, max_candidates=3))
    assert [row.provider_id for row in rows] == ["1234567890"]
    media = await adapter.direct_media(rows[0])
    assert media and media.mime == "video/mp4" and media.url.endswith("movie.mp4")
    assert media.allowed_hosts == OK_HOSTS


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
    assert media.allowed_hosts == DZEN_HOSTS
    assert adapter.cookies is adapter.client.cookies
    assert not any(host in media.allowed_hosts for host in ("yandex.ru", ".yandex.net"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,path",
    [
        (OkRu, "/video/search"),
        (Dzen, "/api/web/v1/zen-search"),
    ],
)
async def test_redirects_are_not_followed(adapter, path):
    routes = {("GET", path): (302, "", "text/plain")}
    if adapter is Dzen:
        routes[("GET", "/search")] = (302, "", "text/plain")
    instance = adapter(client=client(routes))
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


@pytest.mark.asyncio
async def test_ok_ru_search_paginates_with_bounded_post():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200, text=fixture("ok_search.html"), headers={"content-type": "text/html"}
            )
        return httpx.Response(
            200,
            json={"items": [{"id": "9876543210", "title": "Второй дождь", "duration": 9}]},
        )

    adapter = OkRu(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = await adapter.search("дождь", "ru", SearchLimits(max_pages=2, max_candidates=5))
    assert [row.provider_id for row in rows] == ["1234567890", "9876543210"]
    assert seen == [
        ("GET", "/video/search"),
        ("POST", "/web-api/v2/video/fetchSearchResult"),
    ]


@pytest.mark.asyncio
async def test_vk_catalog_paginates_with_section_cursor(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", ("anon", 9999))
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("catalog.getVideoSearchWeb2"):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "items": [{"owner_id": -7, "id": 1, "title": "Rain one"}],
                        "section": {"id": "videos", "next_from": "cursor-2"},
                    }
                },
            )
        return httpx.Response(
            200,
            json={"response": {"items": [{"owner_id": -7, "id": 2, "title": "Rain two"}]}},
        )

    adapter = VKVideo(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=lambda: 0
    )
    rows = await adapter.search("rain", "en", SearchLimits(max_pages=2, max_candidates=5))
    assert [row.provider_id for row in rows] == ["-7_1", "-7_2"]
    assert calls[1].url.path.endswith("catalog.getSection")
    assert calls[1].url.params["section_id"] == "videos"
    assert calls[1].url.params["start_from"] == "cursor-2"


@pytest.mark.asyncio
async def test_dzen_falls_back_to_bounded_html_search():
    html = '<script>window.__data={"items":[{"videoId":"fallback-42","title":"Дождь","duration":8}]};</script>'
    adapter = Dzen(
        client=client(
            {
                ("GET", "/api/web/v1/zen-search"): (200, "<html>not JSON</html>", "text/html"),
                ("GET", "/search"): (200, html, "text/html"),
            }
        )
    )
    rows = await adapter.search("дождь", "ru", SearchLimits())
    assert [row.provider_id for row in rows] == ["fallback-42"]


@pytest.mark.asyncio
async def test_vk_token_expiry_renewal_and_concurrent_singleflight(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", None)
    now = [100.0]
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"token": f"token-{calls}", "expired_at": now[0] + 120},
        )

    adapter = VKVideo(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=lambda: now[0]
    )
    assert await asyncio.gather(adapter._token(), adapter._token()) == ["token-1", "token-1"]
    assert calls == 1
    now[0] += 30
    assert await adapter._token() == "token-1"
    now[0] += 40
    assert await adapter._token() == "token-2"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,error",
    [
        (5, AuthRequired),
        (27, AuthRequired),
        (28, AuthRequired),
        (6, RateLimited),
        (9, RateLimited),
        (29, RateLimited),
    ],
)
async def test_vk_api_error_mapping(monkeypatch, code, error):
    monkeypatch.setattr(vk_video, "_TOKEN", ("anon", 9999))
    adapter = VKVideo(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"error": {"error_code": code}})
            )
        ),
        clock=lambda: 0,
    )
    with pytest.raises(error):
        await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_vk_login_non_json_is_typed_provider_error(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", None)
    adapter = VKVideo(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, text="login unavailable", headers={"content-type": "text/html"}
                )
            )
        )
    )
    with pytest.raises(ProviderError, match="non-JSON"):
        await adapter._token()


@pytest.mark.asyncio
async def test_vk_and_dzen_mp4_refs_are_allowlisted(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", ("anon", 9999))
    vk = VKVideo(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "response": {
                            "items": [
                                {
                                    "owner_id": -7,
                                    "id": 42,
                                    "title": "Rain",
                                    "files": {"mp4_720": "https://stream.vkuser.net/video.mp4"},
                                }
                            ]
                        }
                    },
                )
            )
        ),
        clock=lambda: 0,
    )
    vk_ref = await vk.direct_media(
        Candidate("vk_video", "-7_42", "rain", "en", "https://vkvideo.ru/video-7_42", "Rain", "en")
    )
    dzen = Dzen(
        client=client(
            {
                ("GET", "/video/watch/dzen-video-42"): (
                    200,
                    '<script>x={"videoMetaResponse":{"mp4":"https://strm.yandex.ru/movie.mp4"}}</script>',
                    "text/html",
                )
            }
        )
    )
    dzen_ref = await dzen.direct_media(
        Candidate(
            "dzen",
            "dzen-video-42",
            "rain",
            "en",
            "https://dzen.ru/video/watch/dzen-video-42",
            "Rain",
            "en",
        )
    )
    assert vk_ref and vk_ref.mime == "video/mp4" and ".vkuser.net" in vk_ref.allowed_hosts
    assert dzen_ref and dzen_ref.mime == "video/mp4" and dzen_ref.allowed_hosts == DZEN_HOSTS


@pytest.mark.asyncio
async def test_vk_and_dzen_reject_stream_hosts_outside_allowlist(monkeypatch):
    monkeypatch.setattr(vk_video, "_TOKEN", ("anon", 9999))
    vk = VKVideo(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "response": {
                            "items": [
                                {
                                    "owner_id": -7,
                                    "id": 42,
                                    "title": "Rain",
                                    "files": {
                                        "mp4_720": "https://vkuser.net.attacker.example/video.mp4"
                                    },
                                }
                            ]
                        }
                    },
                )
            )
        ),
        clock=lambda: 0,
    )
    assert (
        await vk.direct_media(
            Candidate(
                "vk_video", "-7_42", "rain", "en", "https://vkvideo.ru/video-7_42", "Rain", "en"
            )
        )
        is None
    )
    dzen = Dzen(
        client=client(
            {
                ("GET", "/video/watch/dzen-video-42"): (
                    200,
                    '<script>x={"videoMetaResponse":{"mp4":"https://yandex.ru/movie.mp4"}}</script>',
                    "text/html",
                )
            }
        )
    )
    assert (
        await dzen.direct_media(
            Candidate(
                "dzen",
                "dzen-video-42",
                "rain",
                "en",
                "https://dzen.ru/video/watch/dzen-video-42",
                "Rain",
                "en",
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_download_redirect_outside_provider_allowlist_is_rejected(tmp_path):
    requests = []

    def handler(request):
        requests.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/video.mp4"})

    with pytest.raises(DownloadError, match="outside provider allowlist"):
        await download_media(
            "https://cdn.okcdn.ru/movie.mp4",
            tmp_path,
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            resolver=lambda _host: ["8.8.8.8"],
            allowed_hosts=OK_HOSTS,
        )
    assert requests == ["cdn.okcdn.ru"]


@pytest.mark.asyncio
async def test_provider_response_size_and_timeout_are_typed():
    oversized = OkRu(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    text="small",
                    headers={"content-type": "text/html", "content-length": str(3 * 1024 * 1024)},
                )
            )
        )
    )
    with pytest.raises(ProviderError, match="size limit"):
        await oversized.search("rain", "en", SearchLimits(max_pages=1))

    def timeout(_request):
        raise httpx.ReadTimeout("slow")

    slow = OkRu(client=httpx.AsyncClient(transport=httpx.MockTransport(timeout)))
    with pytest.raises(ProviderTimeout):
        await slow.search("rain", "en", SearchLimits(max_pages=1))


@pytest.mark.asyncio
async def test_mock_transport_name_does_not_bypass_peer_verification():
    from clipoteca.providers.http import fetch_response

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=MockTransport()) as test_client:
        with pytest.raises(ProviderError, match="peer address unavailable"):
            await fetch_response(
                test_client,
                "https://dzen.ru/api/web/v1/zen-search",
                resolver=lambda _host: ["8.8.8.8"],
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "expected"),
    [(429, RateLimited), (401, AuthRequired)],
)
async def test_dzen_http_errors_propagate_without_html_fallback(http_status, expected):
    called = []

    def handler(request):
        called.append(request.url.path)
        assert request.url.path == "/api/web/v1/zen-search"
        return httpx.Response(
            http_status,
            json={"detail": "do not interpret untrusted error body"},
            headers={"Retry-After": "0"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as test_client:
        with pytest.raises(expected):
            await Dzen(client=test_client).search("rain", "en", SearchLimits())
    assert called == ["/api/web/v1/zen-search"] * (3 if http_status == 429 else 1)


@pytest.mark.asyncio
async def test_dzen_timeout_propagates_without_html_fallback():
    called = []

    def handler(request):
        called.append(request.url.path)
        assert request.url.path == "/api/web/v1/zen-search"
        raise httpx.ReadTimeout("simulated timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as test_client:
        with pytest.raises(ProviderTimeout):
            await Dzen(client=test_client).search("rain", "en", SearchLimits())
    assert called == ["/api/web/v1/zen-search"]
