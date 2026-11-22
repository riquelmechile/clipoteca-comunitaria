"""M24 r7 regression: DNS-validated IP is the actual HTTPS peer."""

from __future__ import annotations

import ssl

import httpx
import pytest

from clipoteca.providers.base import ProviderError
from clipoteca.providers.http import fetch_response
from clipoteca.security.download import DownloadError, download_media
from clipoteca.security.pinning import matches_validated_ip


class Peer:
    def __init__(self, address: str):
        self.address = address

    def get_extra_info(self, key: str):
        assert key == "server_addr"
        return (self.address, 443)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return self.handler(request)


@pytest.mark.asyncio
async def test_provider_dns_rotates_but_connection_uses_original_validated_ip():
    lookups = 0

    def resolver(host):
        nonlocal lookups
        assert host == "cdn.example.com"
        lookups += 1
        return ["8.8.8.8"] if lookups == 1 else ["1.1.1.1"]

    def handler(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "cdn.example.com"
        assert request.extensions["sni_hostname"] == "cdn.example.com"
        return httpx.Response(
            200,
            content=b"ok",
            extensions={"network_stream": Peer("8.8.8.8")},
        )

    transport = RecordingTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await fetch_response(client, "https://cdn.example.com/search", resolver=resolver)
    assert response.text == "ok"
    assert len(transport.requests) == 1
    assert lookups == 1


@pytest.mark.asyncio
async def test_provider_tries_next_validated_ip_without_new_dns():
    lookups = 0

    def resolver(_host):
        nonlocal lookups
        lookups += 1
        return ["8.8.8.8", "1.1.1.1"]

    def handler(request):
        if request.url.host == "8.8.8.8":
            raise httpx.ConnectError("simulated connect failure", request=request)
        assert request.url.host == "1.1.1.1"
        assert request.extensions["sni_hostname"] == "cdn.example.com"
        return httpx.Response(200, content=b"ok", extensions={"network_stream": Peer("1.1.1.1")})

    transport = RecordingTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await fetch_response(client, "https://cdn.example.com/a", resolver=resolver)
    assert response.text == "ok"
    assert [r.url.host for r in transport.requests] == ["8.8.8.8", "1.1.1.1"]
    assert lookups == 1


@pytest.mark.asyncio
async def test_provider_rejects_private_dns_before_any_connection():
    transport = RecordingTransport(lambda _: pytest.fail("must not connect"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ProviderError, match="URL rejected"):
            await fetch_response(
                client, "https://cdn.example.com/a", resolver=lambda _: ["192.168.1.1"]
            )
    assert not transport.requests


@pytest.mark.asyncio
async def test_download_pins_ip_on_each_redirect_and_retains_tls_host(tmp_path):
    resolved = []

    def resolver(host):
        resolved.append(host)
        if host == "media.example.com":
            return ["8.8.8.8"]
        if host == "cdn.example.com":
            return ["1.1.1.1"]
        pytest.fail("unexpected hostname")

    def handler(request):
        if request.url.host == "8.8.8.8":
            assert request.headers["host"] == "media.example.com"
            assert request.extensions["sni_hostname"] == "media.example.com"
            return httpx.Response(
                302,
                headers={"location": "https://cdn.example.com/final.mp4"},
                extensions={"network_stream": Peer("8.8.8.8")},
            )
        assert request.url.host == "1.1.1.1"
        assert request.headers["host"] == "cdn.example.com"
        assert request.extensions["sni_hostname"] == "cdn.example.com"
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=bytes.fromhex("000000186674797069736f6d"),
            extensions={"network_stream": Peer("1.1.1.1")},
        )

    transport = RecordingTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        path = await download_media(
            "https://media.example.com/a", tmp_path, client=client, resolver=resolver
        )
    assert path.read_bytes()[4:8] == b"ftyp"
    assert resolved == ["media.example.com", "cdn.example.com"]


@pytest.mark.asyncio
async def test_download_rejects_private_dns_before_connection(tmp_path):
    transport = RecordingTransport(lambda _: pytest.fail("must not connect"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(DownloadError, match="media fetch rejected"):
            await download_media(
                "https://cdn.example.com/file.mp4",
                tmp_path,
                client=client,
                resolver=lambda _: ["127.0.0.1"],
            )
    assert not transport.requests


@pytest.mark.asyncio
async def test_ipv6_expanded_peer_matches_canonical_validated_ip():
    ipv6 = "2606:4700:4700::1111"
    expanded = "2606:4700:4700:0:0:0:0:1111"
    assert matches_validated_ip(expanded, [ipv6], normalize_ipv6=True)

    def handler(request):
        assert request.url.host == ipv6
        assert request.headers["host"] == "cdn.example.com"
        assert request.extensions["sni_hostname"] == "cdn.example.com"
        return httpx.Response(200, content=b"ok", extensions={"network_stream": Peer(expanded)})

    transport = RecordingTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_response(
            client, "https://cdn.example.com/a", resolver=lambda _: [ipv6]
        )
    assert result.status_code == 200


def test_default_https_transport_keeps_certificate_and_hostname_verification():
    from clipoteca.security.pinning import secure_pinned_transport

    transport = secure_pinned_transport()
    ctx = transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
