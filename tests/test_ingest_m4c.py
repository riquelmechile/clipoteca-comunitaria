from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from clipoteca.catalog import GitHubCatalogWriter
from clipoteca.ingest import ingest
from clipoteca.jobs import JobStore
from clipoteca.providers.base import (
    Adapter,
    Candidate,
    MediaRef,
    ProviderError,
    RateLimited,
    SearchLimits,
    SupportStatus,
)
from clipoteca.providers.nasa import NASA
from clipoteca.security.download import DownloadError, download_media
from clipoteca.security.ssrf import UnsafeMediaURL, validate_public_url
from clipoteca.storage.github import ReleaseAsset, ReleaseBatch
from tests.fake_secrets import fake_secret


FAKE_TOKEN = fake_secret("ingest")


def api_client(mapping):
    def handler(request):
        value = mapping.get(request.url.path)
        if isinstance(value, int):
            return httpx.Response(value, json={"error": "provider_error"})
        if value is None:
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json=value)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_nasa_search_asset_manifest_and_failures():
    adapter = NASA(
        client=api_client(
            {
                "/search": {
                    "collection": {
                        "items": [
                            {
                                "href": "https://images.nasa.gov/details/ABC",
                                "data": [
                                    {"nasa_id": "ABC", "title": "Rain", "media_type": "video"}
                                ],
                            },
                            {
                                "data": [
                                    {"nasa_id": "IMG", "title": "Not video", "media_type": "image"}
                                ]
                            },
                        ]
                    }
                },
                "/asset/ABC": {
                    "collection": {
                        "items": [
                            {"href": "https://nasa.gov/rain.jpg"},
                            {"href": "https://images-assets.nasa.gov/rain~orig.mp4"},
                            {"href": "https://images-assets.nasa.gov/rain~small.mp4"},
                        ]
                    }
                },
            }
        )
    )
    found = await adapter.search("rain", "en", SearchLimits())
    assert len(found) == 1 and found[0].provider_id == "ABC"
    media = await adapter.direct_media(found[0])
    assert media and media.url.endswith("rain~small.mp4")
    for code, error in [(429, RateLimited), (500, ProviderError)]:
        bad = NASA(client=api_client({"/search": code}))
        with pytest.raises(error):
            await bad.search("rain", "en", SearchLimits())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://public.example.com/file.mp4",
        "https://localhost/a.mp4",
        "https://127.0.0.1/video.mp4",
        "https://169.254.169.254/latest/meta-data",
        "https://[fd00::1]/video.mp4",
        "https://metadata.google.internal/video.mp4",
        "https://user:secret@public.example.com/a.mp4",
        "https://public.example.com:8080/a.mp4",
    ],
)
async def test_ssrf_rejects_unsafe_targets(url):
    with pytest.raises(UnsafeMediaURL):
        await validate_public_url(url, resolver=lambda _: ["8.8.8.8"])


@pytest.mark.asyncio
async def test_ssrf_rejects_private_dns_even_if_one_public_answer():
    with pytest.raises(UnsafeMediaURL, match="DNS"):
        await validate_public_url(
            "https://videos.example.com/file.mp4",
            resolver=lambda _: ["8.8.8.8", "10.0.0.1"],
        )
    assert await validate_public_url(
        "https://cdn.example.com/movie.mp4",
        resolver=lambda _: ["8.8.8.8"],
    )


@pytest.mark.asyncio
async def test_bounded_stream_checks_magic_size_html_and_redirect(tmp_path):
    valid = bytes.fromhex("000000186674797069736f6d") + b"12345678"

    def resolver(host):
        return ["8.8.8.8"]

    def transport(req):
        if req.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://127.0.0.1/hidden"})
        if req.url.path == "/html":
            return httpx.Response(
                200, text="<html>fake</html>", headers={"content-type": "text/html"}
            )
        if req.url.path == "/huge":
            return httpx.Response(
                200, content=valid, headers={"content-type": "video/mp4", "content-length": "99999"}
            )
        return httpx.Response(200, content=valid, headers={"content-type": "video/mp4"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    fetched = await download_media(
        "https://cdn.example.com/valid",
        tmp_path,
        client=client,
        resolver=resolver,
        max_bytes=100,
    )
    assert fetched.read_bytes() == valid
    fetched.unlink()
    for path in ("html", "huge", "redirect"):
        with pytest.raises(DownloadError):
            await download_media(
                f"https://cdn.example.com/{path}",
                tmp_path,
                client=client,
                resolver=resolver,
                max_bytes=100,
            )
    assert list(tmp_path.iterdir()) == []


class FixtureAdapter(Adapter):
    category = "stock_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name: str):
        self.name = name

    async def search(self, term: str, lang: str, limits: SearchLimits):
        if lang != "en":
            return []
        return [
            Candidate(
                self.name,
                "source-1",
                term,
                lang,
                f"https://{self.name}.example.org/source/1",
                "rain",
                "en",
                metadata={"description": "rain"},
            )
        ]

    async def metadata(self, candidate):
        return candidate.metadata

    async def direct_media(self, candidate):
        return MediaRef(f"https://cdn.example.com/{self.name}.mp4", "video/mp4")


class FakeStorage:
    def __init__(self):
        self.assets = {}

    def get_or_create_batch_release(self, day):
        return ReleaseBatch(10, "batch-2026-11-20-001", "https://uploads.github.com")

    def upload_asset(self, release, path, name):
        data = Path(path).read_bytes()
        asset = ReleaseAsset(
            len(self.assets) + 1,
            name,
            len(data),
            "uploaded",
            f"https://github.com/test/releases/download/batch/{name}",
        )
        self.assets[name] = (asset, hashlib.sha256(data).hexdigest())
        return asset

    def verify_asset(self, name, size, digest, release_id):
        asset, actual = self.assets[name]
        assert actual == digest and asset.size == size and release_id == 10
        return asset


@pytest.mark.asyncio
async def test_two_provider_real_ffmpeg_to_mock_github_publication(tmp_path):
    repo = "riquelmechile/clipoteca-comunitaria"
    catalog: list[dict] = []
    revision = 0
    put_count = 0

    def github(request):
        nonlocal revision, put_count, catalog
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sha": f"catalog-{revision}",
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(catalog).encode()).decode(),
                },
            )
        if request.method == "PUT":
            payload = json.loads(request.content)
            assert payload["sha"] == f"catalog-{revision}"
            catalog = json.loads(base64.b64decode(payload["content"]))
            revision += 1
            put_count += 1
            return httpx.Response(200, json={"commit": {"sha": f"published-{revision}"}})
        raise AssertionError(request.url)

    writer = GitHubCatalogWriter(
        repo,
        FAKE_TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(github)),
    )
    storage = FakeStorage()

    async def fake_downloader(url: str, root: Path) -> Path:
        target = root / "download.mp4"
        size = "320x180" if "pexels" in url else "240x240"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={size}:rate=12:duration=0.5",
                "-c:v",
                "libx264",
                str(target),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return target

    params = {
        "repository": repo,
        "token": FAKE_TOKEN,
        "job_root": tmp_path / "jobs",
        "writer": writer,
        "storage": storage,
        "downloader": fake_downloader,
        "max_results": 2,
        "translator": lambda text, src, dst: "Lluvia" if dst == "es" else "Rain",
    }
    report = await ingest("lluvia", [FixtureAdapter("pexels"), FixtureAdapter("pixabay")], **params)
    assert report.status == "published", report.errors
    assert len(report.published_ids) == 2, report
    assert len(catalog) == 2 and put_count == 2
    assert {clip["source"]["provider"] for clip in catalog} == {"pexels", "pixabay"}
    assert all(clip["title"] == {"es": "Lluvia", "en": "Rain"} for clip in catalog)
    assert all(clip["media"]["duration_seconds"] <= 20 for clip in catalog)
    job = JobStore(tmp_path / "jobs").load(report.request_id)
    assert job["status"] == "published"
    assert job["language_counts"] == {"es": 2, "en": 2, "zh": 2, "ru": 2}
    again = await ingest("lluvia", [FixtureAdapter("pexels"), FixtureAdapter("pixabay")], **params)
    assert again.status == "duplicate" and put_count == 2


def test_workflow_only_writes_on_main():
    workflow = Path(__file__).parents[1] / ".github/workflows/ingest.yml"
    source = workflow.read_text()
    assert "github.ref == 'refs/heads/main'" in source
    assert "group: clipoteca-ingest-" in source
    assert "group: clipoteca-catalog" not in source
    assert "contents: write" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_sources", [1, 3])
async def test_v206_publication_round_robins_providers_without_lowering_quality(
    tmp_path, valid_sources
):
    """Three genuine publication slots: diversity when available, fallback when not."""
    repo = "riquelmechile/clipoteca-comunitaria"
    catalog: list[dict] = []
    revision = 0

    def github(request):
        nonlocal revision, catalog
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sha": f"catalog-{revision}",
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(catalog).encode()).decode(),
                },
            )
        if request.method == "PUT":
            payload = json.loads(request.content)
            assert payload["sha"] == f"catalog-{revision}"
            catalog = json.loads(base64.b64decode(payload["content"]))
            revision += 1
            return httpx.Response(200, json={"commit": {"sha": f"published-{revision}"}})
        raise AssertionError(request.method)

    class Multiple(FixtureAdapter):
        async def search(self, term, lang, limits):
            if lang != "en":
                return []
            counts = 5 if self.name == "pexels" and valid_sources == 1 else 1
            return [
                Candidate(
                    self.name,
                    f"source-{n}",
                    term,
                    lang,
                    f"https://{self.name}.example.org/source/{n}",
                    f"rain falling scene from {self.name} number {n}",
                    "en",
                    metadata={"description": f"rain landscape from {self.name} number {n}"},
                )
                for n in range(counts)
            ]

        async def direct_media(self, candidate):
            if self.name != "pexels" and valid_sources == 1:
                return None
            return MediaRef(
                f"https://cdn.example.org/{self.name}/{candidate.provider_id}.mp4",
                "video/mp4",
            )

    # Different source footage shapes protect the real perceptual-dedup gate:
    # tests do not mock or weaken normalization, scene quality or catalog CAS.
    async def downloader(url: str, root: Path):
        output = root / "source.mp4"
        patterns = ["320x180", "240x240", "180x320", "400x200", "320x240", "400x300", "300x400"]
        if "pixabay" in url:
            index = 1
        elif "wikimedia" in url:
            index = 2
        elif "source-2" in url:
            index = 3
        elif "source-1" in url and valid_sources == 1:
            index = 4
        elif "source-3" in url:
            index = 5
        elif "source-4" in url:
            index = 6
        else:
            index = 0
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={patterns[index]}:rate=12:duration=0.8",
                "-c:v",
                "libx264",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return output

    writer = GitHubCatalogWriter(
        repo,
        FAKE_TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(github)),
    )
    report = await ingest(
        "lluvia",
        [Multiple("pexels"), Multiple("pixabay"), Multiple("wikimedia_commons")],
        repository=repo,
        token=FAKE_TOKEN,
        job_root=tmp_path / "jobs",
        writer=writer,
        storage=FakeStorage(),
        downloader=downloader,
        max_results=3,
        max_clips_per_source=2,
        translator=lambda text, src, dst: f"Escena de {text}"
        if dst == "es"
        else f"Scene of {text}",
    )
    assert report.status == "published", report.errors
    assert len(report.published_ids) == 3, report
    assert len(catalog) == 3
    contributing = {clip["source"]["provider"] for clip in catalog}
    assert len(contributing) > 1 if valid_sources == 3 else contributing == {"pexels"}
