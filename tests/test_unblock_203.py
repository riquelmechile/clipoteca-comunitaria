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
