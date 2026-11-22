"""M24 r5: regression tests for real Archive HTML echo and VK token envelope."""

from __future__ import annotations

import json
from pathlib import Path
from html import escape

import httpx
import pytest

from clipoteca.providers.base import AuthRequired, ProviderError, SearchLimits
from clipoteca.providers.internet_archive import InternetArchive, build_query
from clipoteca.providers import vk_video
from clipoteca.providers.vk_video import VKVideo

FIXTURES = Path(__file__).parent / "fixtures" / "m24r5"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="ascii"))


@pytest.mark.asyncio
@pytest.mark.parametrize("echo_field", ["qin", "q"])
async def test_archive_accepts_html_escaped_original_echo_not_normalized_query(echo_field):
    response = fixture("archive_escaped_echo.json")
    params = response["responseHeader"]["params"]
    original = build_query(["rain"], all_terms=True)
    assert params["qin"] == escape(original, quote=True)
    assert params["query"] != original
    if echo_field == "q":
        params["q"] = params.pop("qin")

    def handler(request):
        assert request.url.params["q"] == original
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        rows = await adapter.search("rain", "en", SearchLimits(max_pages=1))
    assert [row.provider_id for row in rows] == ["rain_movie"]


@pytest.mark.asyncio
async def test_archive_rejects_real_html_mismatched_original_echo():
    response = fixture("archive_escaped_echo.json")
    response["responseHeader"]["params"]["qin"] = "title:&quot;not-rain&quot;"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        adapter = InternetArchive(client=client, min_interval_seconds=0)
        with pytest.raises(ProviderError, match="mismatched query echo"):
            await adapter.search("rain", "en", SearchLimits(max_pages=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_field", ["expired_at", "expires"])
async def test_vk_nested_access_token_and_expiry_are_read_without_logging(
    monkeypatch, caplog, capsys, expiry_field
):
    data = fixture("vk_nested_anonym_token.json")
    token = data["data"]["access_token"]
    if expiry_field == "expires":
        data["data"].pop("expired_at")
        data["data"]["expires"] = 1700001200

    monkeypatch.setattr(vk_video, "_TOKEN", None)
    monkeypatch.setattr(vk_video, "_TOKEN_BACKOFF_UNTIL", 0.0)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "login.vk.com":
            assert request.method == "POST"
            assert request.url.params["act"] == "get_anonym_token"
            return httpx.Response(200, json=data)
        assert request.url.host == "api.vkvideo.ru"
        assert request.url.params["access_token"] == token
        return httpx.Response(200, json={"error": {"error_code": 5}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = VKVideo(token="", client=client, clock=lambda: 1700000000.0)
        assert await adapter._token() == token
        assert vk_video._TOKEN == (token, 1700001200.0)
        with pytest.raises(AuthRequired, match="VK token lacks required permissions") as err:
            await adapter.search("rain", "en", SearchLimits(max_pages=1))

    assert len(requests) == 2
    assert token not in str(err.value)
    assert token not in caplog.text
    output = capsys.readouterr()
    assert token not in output.out + output.err
