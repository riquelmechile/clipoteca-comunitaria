"""2.0.1 download smoke: real-media workflow is opt-in and never publishes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipoteca.providers.base import Candidate, MediaRef
from clipoteca.security.download import DownloadError


@pytest.fixture
def smoke():
    path = Path(__file__).resolve().parents[1] / "scripts/smoke_sources.py"
    spec = importlib.util.spec_from_file_location("smoke_201", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate():
    return Candidate("dzen", "abc", "rain", "en", "https://dzen.ru/video/watch/abc", "Rain", "en")


@pytest.mark.asyncio
async def test_download_probe_one_candidate_then_temp_is_removed(smoke, monkeypatch):
    calls = []
    downloaded = []

    class Adapter:
        accepts_text_languages = ("en",)

        async def search(self, term, lang, limits):
            calls.append(("search", term, lang, limits.max_candidates))
            return [candidate(), candidate()]

        async def direct_media(self, item):
            calls.append(("direct_media", item.provider_id))
            return MediaRef("https://cdn.example.com/video.mp4", "video/mp4")

    async def fake_download(media, temp):
        assert media.url.endswith("video.mp4")
        assert temp.is_dir()
        target = temp / "sample.mp4"
        target.write_bytes(bytes.fromhex("000000186674797069736f6d"))
        downloaded.append(target)
        return target

    monkeypatch.setattr(smoke, "_download_candidate", fake_download)
    row = await smoke.download_probe(SimpleNamespace(name="dzen", requires=(), adapter=Adapter))
    assert row["status"] == "DOWNLOAD_OK" and row["ok"] is True
    assert row["download_bytes"] == 12
    assert calls == [("search", "rain", "en", 1), ("direct_media", "abc")]
    assert not downloaded[0].exists()
    assert "url" not in row and "candidate" not in row


@pytest.mark.asyncio
async def test_download_no_direct_media_is_not_promoted(smoke):
    class Adapter:
        accepts_text_languages = ("en",)

        async def search(self, *_args):
            return [candidate()]

        async def direct_media(self, _candidate):
            return None

    row = await smoke.download_probe(SimpleNamespace(name="dzen", requires=(), adapter=Adapter))
    assert row["status"] == "NO_DIRECT_MEDIA" and not row["ok"]


@pytest.mark.asyncio
async def test_download_missing_required_token_does_not_construct_adapter(smoke):
    def no_adapter():
        pytest.fail("adapter must not be constructed without required key")

    row = await smoke.download_probe(
        SimpleNamespace(
            name="youtube", requires=("MISSING_CLIPOTECA_TEST_KEY",), adapter=no_adapter
        )
    )
    assert row["status"] == "AUTH_REQUIRED" and row["elapsed_ms"] == 0


@pytest.mark.asyncio
async def test_download_error_does_not_store_urls_or_secrets(smoke, monkeypatch):
    class Adapter:
        accepts_text_languages = ("en",)

        async def search(self, *_args):
            return [candidate()]

        async def direct_media(self, _candidate):
            return MediaRef("https://private.example/video.mp4", "video/mp4")

    async def rejected(_media, _tmp):
        raise DownloadError("request https://private.example/?token=SECRET")

    monkeypatch.setattr(smoke, "_download_candidate", rejected)
    row = await smoke.download_probe(SimpleNamespace(name="dzen", requires=(), adapter=Adapter))
    assert row["status"] == "DOWNLOAD_ERROR" and not row["ok"]
    assert row["error_detail"] == "redacted"
    assert "https://" not in json.dumps(row) and "SECRET" not in json.dumps(row)


@pytest.mark.asyncio
async def test_download_hls_uses_short_cut_and_low_byte_cap(smoke, monkeypatch, tmp_path):
    media = MediaRef(
        "https://cdn.example.com/master.m3u8",
        "application/vnd.apple.mpegurl",
        allowed_hosts=("cdn.example.com",),
        referer="https://dzen.ru/video/watch/abc",
    )
    calls = []

    async def fake_hls(url, stage, **kwargs):
        calls.append((url, stage, kwargs))
        target = stage / "clip.mp4"
        target.write_bytes(bytes.fromhex("000000186674797069736f6d"))
        return target

    monkeypatch.setattr(smoke, "download_hls", fake_hls)
    path = await smoke._download_candidate(media, tmp_path)
    assert path.exists()
    assert calls[0][2]["max_bytes"] == 8 * 1024 * 1024
    assert calls[0][2]["max_duration"] == 20.0
    assert calls[0][2]["referer"] == "https://dzen.ru/video/watch/abc"


@pytest.mark.asyncio
async def test_download_mp4_is_local_trim_and_bounded(smoke, monkeypatch, tmp_path):
    media = MediaRef("https://cdn.example.com/file.mp4", "video/mp4")
    seen = []

    async def fake_download(url, directory, **kwargs):
        seen.append((url, kwargs))
        path = directory / "incoming.bin"
        path.write_bytes(bytes.fromhex("000000186674797069736f6d"))
        return path

    async def fake_trim(source, destination):
        assert source.is_file() and source.parent == destination.parent
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(smoke, "download_media", fake_download)
    monkeypatch.setattr(smoke, "_cut_local_clip", fake_trim)
    out = await smoke._download_candidate(media, tmp_path)
    assert out.name.endswith(".mkv")
    assert seen[0][1]["max_bytes"] == 8 * 1024 * 1024
    assert seen[0][1]["total_seconds"] == 60.0


def test_download_mode_is_explicit_and_all_is_opt_in(smoke, monkeypatch, tmp_path):
    called = {"providers": 0, "candidates": 0, "downloads": 0}

    async def provider():
        called["providers"] += 1
        return {"mode": "live_network_probes", "total_probes": 1, "elapsed_seconds_measured": 0.0}

    async def candidates():
        called["candidates"] += 1
        return {"mode": "candidates", "total_probes": 0}

    async def downloads():
        called["downloads"] += 1
        return {
            "mode": "real_download_probes",
            "total_probes": 1,
            "elapsed_seconds_measured": 0.0,
            "provider_downloads": [{"provider": "dzen", "ok": True}],
        }

    monkeypatch.setattr(smoke, "main_async", provider)
    monkeypatch.setattr(smoke, "candidates_smoke", candidates)
    monkeypatch.setattr(smoke, "downloads_smoke", downloads)

    path = tmp_path / "report.json"
    assert smoke.main(["--mode", "all", "--output", str(path)]) == 0
    assert called == {"providers": 1, "candidates": 1, "downloads": 0}
    assert smoke.main(["--mode", "downloads", "--output", str(path)]) == 0
    assert called["downloads"] == 1
    assert json.loads(path.read_text())["mode"] == "real_download_probes"
    assert smoke.main(["--mode", "all", "--include-downloads", "--output", str(path)]) == 0
    assert called == {"providers": 2, "candidates": 2, "downloads": 2}
    assert json.loads(path.read_text())["total_download_probes"] == 1
    with pytest.raises(SystemExit):
        smoke.main(["--mode", "providers", "--include-downloads"])


def test_worker_raw_defaults_and_source_url_without_wrangler():
    root = Path(__file__).resolve().parents[1]
    catalog = (root / "worker/src/catalog.ts").read_text(encoding="ascii")
    jobs = (root / "worker/src/jobs.ts").read_text(encoding="ascii")
    sources = (root / "worker/src/mcp.ts").read_text(encoding="ascii")
    assert "env.CATALOG_URL ??" in catalog
    assert '"https://raw.githubusercontent.com/" + repo + "/main/data/clips.json"' in catalog
    assert "const prefix = env.CATALOG_JOBS_URL" in jobs
    assert '"https://raw.githubusercontent.com/" + repo + "/main/data/jobs/" + id + ".json"' in jobs
    assert '"https://raw.githubusercontent.com/" + repo + "/main/data/sources.json"' in sources
