"""Rebinding and total-download-deadline regression tests."""

import asyncio

import httpx
import pytest

from clipoteca.security.download import DownloadError, download_media


class Peer:
    def __init__(self, ip):
        self.ip = ip

    def get_extra_info(self, key):
        if key == "server_addr":
            return self.ip, 443
        return None


@pytest.mark.asyncio
async def test_rebinding_rejects_connected_private_peer_before_reading_body(tmp_path):
    downloaded = False

    def serve(_request):
        nonlocal downloaded
        downloaded = True
        return httpx.Response(
            200,
            content=b"\x00\x00\x00\x18ftypisom",
            headers={"content-type": "video/mp4"},
            extensions={"network_stream": Peer("169.254.169.254")},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    with pytest.raises(DownloadError, match="connected peer"):
        await download_media(
            "https://media.example.com/x.mp4",
            tmp_path,
            client=client,
            resolver=lambda _: ["8.8.8.8"],
        )
    assert downloaded
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_peer_dns_mismatch_even_when_public_is_rejected(tmp_path):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"\x00\x00\x00\x18ftypisom",
                headers={"content-type": "video/mp4"},
                extensions={"network_stream": Peer("1.1.1.1")},
            )
        )
    )
    with pytest.raises(DownloadError, match="connected peer"):
        await download_media(
            "https://media.example.com/x.mp4",
            tmp_path,
            client=client,
            resolver=lambda _: ["8.8.8.8"],
        )


@pytest.mark.asyncio
async def test_total_deadline_expires_with_slow_response(tmp_path):
    async def slow(_request):
        await asyncio.sleep(0.05)
        return httpx.Response(
            200, content=b"\x00\x00\x00\x18ftypisom", headers={"content-type": "video/mp4"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    with pytest.raises(DownloadError, match="total media deadline"):
        await download_media(
            "https://media.example.com/x.mp4",
            tmp_path,
            client=client,
            resolver=lambda _: ["8.8.8.8"],
            total_seconds=0.001,
        )
