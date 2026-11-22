"""2.0.2: OK.ru follows allowlisted redirects; long HLS playlists parse."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from clipoteca.providers.base import ProviderError, SearchLimits
from clipoteca.providers.ok_ru import OkRu
from clipoteca.security.hls import parse_media

FIXTURES = Path(__file__).parent / "fixtures" / "m24r4"


@pytest.mark.asyncio
async def test_okru_search_follows_redirect_inside_allowlist():
    calls = []
    page = (FIXTURES / "ok_ru_search_real_trimmed.html").read_text(encoding="ascii")

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://m.ok.ru/video/search/rain"})
        return httpx.Response(200, text=page)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await OkRu(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert len(calls) == 2
    assert calls[1] == "https://m.ok.ru/video/search/rain"
    assert rows


@pytest.mark.asyncio
async def test_okru_redirect_outside_allowlist_is_not_followed():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="host not allowed"):
            await OkRu(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_okru_redirect_chain_is_bounded():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": f"https://ok.ru/hop{len(calls)}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="redirect limit"):
            await OkRu(client=client).search("rain", "en", SearchLimits(max_pages=1))
    assert len(calls) == 4


def test_long_hls_media_playlist_parses():
    rows = ["#EXTM3U", "#EXT-X-TARGETDURATION:4"]
    for i in range(3000):
        rows += ["#EXTINF:4.0,", f"seg{i}.ts"]
    rows.append("#EXT-X-ENDLIST")
    segments = parse_media("\n".join(rows))
    assert len(segments) == 3000
