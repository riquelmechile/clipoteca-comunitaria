"""2.0.3: Dzen streams on the VK/OK CDN; Bilibili Akamai mirrors."""

from __future__ import annotations

import pytest

from clipoteca.providers.base import ProviderError
from clipoteca.providers.bilibili import _media_host
from clipoteca.providers.dzen import _streams


def test_dzen_stream_on_okcdn_is_selected():
    payload = {
        "oneVideoStreams": [
            {"url": "https://vd716.okcdn.ru/video.m3u8?expires=1&sig=x", "type": "hls"},
            {"url": "https://evil.example.com/video.m3u8", "type": "hls"},
        ]
    }
    assert _streams([payload]).startswith("https://vd716.okcdn.ru/video.m3u8")


def test_dzen_foreign_stream_hosts_are_ignored():
    assert _streams([{"url": "https://evil.example.com/video.m3u8"}]) == ""


def test_bilibili_akamai_mirror_hosts():
    assert _media_host("https://upos-sz-mirrorakam.akamaized.net/a.mp4") == (
        "upos-sz-mirrorakam.akamaized.net"
    )
    with pytest.raises(ProviderError):
        _media_host("https://evil.akamaized.net/a.mp4")


@pytest.mark.asyncio
async def test_hls_probe_sends_provider_browser_user_agent(tmp_path):
    import httpx

    from clipoteca.providers.signing import BROWSER_UA
    from clipoteca.security.hls import download_hls_probe

    payload = bytearray(b"X" * 700)
    payload[0] = payload[188] = 0x47
    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        if request.url.path.endswith("/video.m3u8"):
            return httpx.Response(200, content=b"#EXTM3U\n#EXTINF:2.0,\nseg.ts\n")
        return httpx.Response(200, content=bytes(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await download_hls_probe(
            "https://vd716.okcdn.ru/video.m3u8",
            tmp_path,
            client=client,
            resolver=lambda _host: ["8.8.8.8"],
            max_bytes=400,
            allowed_hosts=("vd716.okcdn.ru",),
            user_agent=BROWSER_UA,
        )
    assert agents == [BROWSER_UA, BROWSER_UA]


@pytest.mark.asyncio
async def test_hls_rejects_header_injection_user_agent(tmp_path):
    import httpx

    from clipoteca.security.download import DownloadError
    from clipoteca.security.hls import download_hls_probe

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)) as client:
        with pytest.raises(DownloadError, match="User-Agent"):
            await download_hls_probe(
                "https://vd716.okcdn.ru/video.m3u8",
                tmp_path,
                client=client,
                resolver=lambda _host: ["8.8.8.8"],
                max_bytes=400,
                user_agent="x\r\nInjected: 1",
            )


def test_dzen_media_ref_carries_browser_user_agent():
    from clipoteca.providers.base import MediaRef

    assert MediaRef("https://a", "video/mp4").user_agent is None
