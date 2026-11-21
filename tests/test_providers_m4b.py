from __future__ import annotations

import httpx
import pytest

from clipoteca.providers.base import Candidate, ProviderError, RateLimited, SearchLimits
from clipoteca.providers.internet_archive import InternetArchive, build_query
from clipoteca.providers.wikimedia_commons import WikimediaCommons


def transport(mapping):
    def handler(request):
        item = mapping.get(request.url.path)
        if isinstance(item, int):
            return httpx.Response(item, json={"error": "failure"})
        if item is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=item)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_wikimedia_only_returns_true_video_file():
    payload = {
        "query": {
            "pages": {
                "12": {
                    "pageid": 12,
                    "ns": 6,
                    "title": "File:Rain.webm",
                    "imageinfo": [
                        {
                            "mime": "video/webm",
                            "url": "https://upload.wikimedia.org/rain.webm",
                            "size": 2048,
                        }
                    ],
                },
                "13": {
                    "pageid": 13,
                    "ns": 6,
                    "title": "File:Rain.jpg",
                    "imageinfo": [
                        {"mime": "image/jpeg", "url": "https://upload.wikimedia.org/rain.jpg"}
                    ],
                },
            }
        }
    }
    adapter = WikimediaCommons(client=transport({"/w/api.php": payload}))
    result = await adapter.search("дождь", "ru", SearchLimits())
    assert len(result) == 1
    assert result[0].origin_id == "wikimedia_commons:12"
    assert (await adapter.direct_media(result[0])).mime == "video/webm"


@pytest.mark.asyncio
async def test_archive_must_resolve_actual_movie_and_skip_giant_files():
    payloads = {
        "/advancedsearch.php": {
            "responseHeader": {"params": {"q": build_query(["rain"], all_terms=True)}},
            "response": {
                "docs": [
                    {"identifier": "city_rain", "title": "Rain in town", "mediatype": "movies"}
                ]
            },
        },
        "/metadata/city_rain": {
            "metadata": {"identifier": "city_rain"},
            "files": [
                {"name": "splash.jpg", "size": "1024"},
                {"name": "rain-4k.mp4", "size": "19999999999"},
                {"name": "rain-small.webm", "size": "3800000"},
                {"name": "readme.html", "size": "1000"},
            ],
        },
    }
    adapter = InternetArchive(client=transport(payloads))
    found = await adapter.search("rain", "en", SearchLimits())
    assert len(found) == 1
    media = await adapter.direct_media(found[0])
    assert media and media.url == "https://archive.org/download/city_rain/rain-small.webm"
    assert media.mime == "video/webm"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_cls,path",
    [
        (WikimediaCommons, "/w/api.php"),
        (InternetArchive, "/advancedsearch.php"),
    ],
)
async def test_empty_and_429_and_500(adapter_cls, path):
    empty = adapter_cls(
        client=transport(
            {
                path: {
                    "query": {"pages": {}},
                    "responseHeader": {"params": {"q": build_query(["rain"], all_terms=True)}},
                    "response": {"docs": []},
                }
            }
        )
    )
    assert await empty.search("rain", "en", SearchLimits()) == []
    for code, error in [(429, RateLimited), (500, ProviderError)]:
        adapter = adapter_cls(client=transport({path: code}))
        with pytest.raises(error):
            await adapter.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
async def test_archive_no_files_means_search_only():
    adapter = InternetArchive(
        client=transport(
            {
                "/metadata/a": {
                    "metadata": {"identifier": "a"},
                    "files": [{"name": "page.html", "size": "1000"}],
                }
            }
        )
    )
    candidate = Candidate(
        "internet_archive", "a", "rain", "en", "https://archive.org/details/a", "Rain", "en"
    )
    assert await adapter.direct_media(candidate) is None
