"""M24 r8: IP pinning must preserve the original cookie origin and URL."""

from __future__ import annotations

import httpx
import pytest

from clipoteca.providers.http import fetch_response
from clipoteca.security.download import download_media
from clipoteca.security.pinning import stream_pinned


class Peer:
    def __init__(self, ip):
        self.ip = ip

    def get_extra_info(self, key):
        assert key == "server_addr"
        return self.ip, 443


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.requests = []
        self.handler = handler

    async def handle_async_request(self, request):
        self.requests.append(request)
        return self.handler(request)


@pytest.mark.asyncio
async def test_native_pinned_client_preserves_dzen_domain_cookies(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "dzen.ru"
        assert request.extensions["sni_hostname"] == "dzen.ru"
        if len(seen) == 1:
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                headers={"set-cookie": "dzen_session=guest; Domain=.dzen.ru; Path=/; Secure"},
                content=b"initial",
                extensions={"network_stream": Peer("8.8.8.8")},
            )
        assert "dzen_session=guest" in request.headers.get("cookie", "")
        return httpx.Response(
            200, content=b"subsequent", extensions={"network_stream": Peer("8.8.8.8")}
        )

    transport = RecordingTransport(handler)
    monkeypatch.setattr("clipoteca.security.pinning.secure_pinned_transport", lambda: transport)
    async with httpx.AsyncClient(trust_env=False) as client:
        first = await fetch_response(
            client, "https://dzen.ru/first", resolver=lambda _: ["8.8.8.8"]
        )
        assert first.request.url.host == "dzen.ru"
        assert first.url.host == "dzen.ru"
        assert any(
            cookie.name == "dzen_session" and cookie.domain.lstrip(".") == "dzen.ru"
            for cookie in client.cookies.jar
        )
        second = await fetch_response(
            client, "https://dzen.ru/second", resolver=lambda _: ["8.8.8.8"]
        )
        assert second.text == "subsequent"
        assert client.cookies.get("dzen_session") == "guest"
        assert len(seen) == 2


@pytest.mark.asyncio
async def test_foreign_transport_does_not_scope_host_cookie_to_pinned_ip():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "8.8.8.8"
        if len(seen) == 1:
            return httpx.Response(
                200,
                content=b"first",
                headers={"set-cookie": "buvid3=fictional; Path=/; Secure"},
                extensions={"network_stream": Peer("8.8.8.8")},
            )
        assert request.headers["host"] == "api.bilibili.com"
        assert "buvid3=fictional" in request.headers.get("cookie", "")
        return httpx.Response(
            200, content=b"second", extensions={"network_stream": Peer("8.8.8.8")}
        )

    async with httpx.AsyncClient(transport=RecordingTransport(handler)) as client:
        first = await fetch_response(
            client,
            "https://api.bilibili.com/first",
            resolver=lambda _: ["8.8.8.8"],
        )
        assert first.url.host == "api.bilibili.com"
        assert all(cookie.domain != "8.8.8.8" for cookie in client.cookies.jar)
        assert any(cookie.domain == "api.bilibili.com" for cookie in client.cookies.jar)
        second = await fetch_response(
            client,
            "https://api.bilibili.com/second",
            resolver=lambda _: ["8.8.8.8"],
        )
        assert second.text == "second"


@pytest.mark.asyncio
async def test_api_bilibili_cookie_does_not_leak_to_cdn(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        if request.headers["host"] == "api.bilibili.com":
            return httpx.Response(
                200,
                content=b"ok",
                headers={"set-cookie": "buvid3=fake; Domain=api.bilibili.com; Path=/; Secure"},
                extensions={"network_stream": Peer("8.8.8.8")},
            )
        assert request.headers["host"] == "cn-cdn.bilivideo.com"
        assert "buvid3" not in request.headers.get("cookie", "")
        return httpx.Response(200, content=b"cdn", extensions={"network_stream": Peer("8.8.8.8")})

    transport = RecordingTransport(handler)
    monkeypatch.setattr("clipoteca.security.pinning.secure_pinned_transport", lambda: transport)
    async with httpx.AsyncClient(trust_env=False) as client:
        await fetch_response(
            client,
            "https://api.bilibili.com/x",
            resolver=lambda _: ["8.8.8.8"],
        )
        assert client.cookies.get("buvid3") == "fake"
        response = await fetch_response(
            client,
            "https://cn-cdn.bilivideo.com/video",
            resolver=lambda _: ["8.8.8.8"],
        )
        assert response.text == "cdn"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_relative_redirect_stays_on_original_domain_and_sends_cookie(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "media.example.com"
        assert request.extensions["sni_hostname"] == "media.example.com"
        if len(requests) == 1:
            assert request.url.path == "/start.mp4"
            return httpx.Response(
                302,
                headers={
                    "location": "/final.mp4",
                    "set-cookie": "media_session=guest; Path=/; Secure",
                },
                extensions={"network_stream": Peer("8.8.8.8")},
            )
        assert request.url.path == "/final.mp4"
        assert "media_session=guest" in request.headers.get("cookie", "")
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=bytes.fromhex("000000186674797069736f6d"),
            extensions={"network_stream": Peer("8.8.8.8")},
        )

    transport = RecordingTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        path = await download_media(
            "https://media.example.com/start.mp4",
            tmp_path,
            client=client,
            resolver=lambda _: ["8.8.8.8"],
        )
        assert path.read_bytes()[4:8] == b"ftyp"
        assert any(cookie.domain == "media.example.com" for cookie in client.cookies.jar)
        assert all(cookie.domain != "8.8.8.8" for cookie in client.cookies.jar)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_pinned_response_exposes_original_url_before_yield():
    def handler(request):
        assert request.url.host == "8.8.8.8"
        return httpx.Response(
            302,
            headers={"location": "/relative-next"},
            extensions={"network_stream": Peer("8.8.8.8")},
        )

    async with httpx.AsyncClient(transport=RecordingTransport(handler)) as client:
        async with stream_pinned(
            client,
            "GET",
            "https://media.example.com/start?q=1",
            addresses=["8.8.8.8"],
        ) as response:
            assert response.url.host == "media.example.com"
            assert response.request.url.host == "media.example.com"
            assert response.url.path == "/start"
            assert response.url.params["q"] == "1"
            assert response.headers["location"] == "/relative-next"
