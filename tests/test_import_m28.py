"""M28 PR3: no-persistence reuse of v2 ingest and verified local scene playback."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipoteca.ingest import IngestReport
from clipoteca.media.select import SourceWindow
from clipoteca.providers.base import Adapter, Candidate, MediaRef, SearchLimits, SupportStatus
from clipoteca.studio import importer
from clipoteca.studio.clipscene import clip_scene
from clipoteca.studio.importer import ImportSceneError, import_scene

FIXTURE = Path(__file__).parent / "fixtures" / "m28c"
FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def load(name):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


class FakeAdapter(Adapter):
    category = "stock_video"
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD

    def __init__(self, name="pexels", entries=None):
        self.name = name
        self.entries = [
            row
            for row in (entries if entries is not None else load("candidatos.json"))
            if row["provider"] == name
        ]

    async def search(self, term: str, lang: str, limits: SearchLimits):
        if lang != "en":
            return []
        return [
            Candidate(
                self.name,
                row["provider_id"],
                term,
                lang,
                row["source_url"],
                row["source_title"],
                "en",
                duration_seconds=8,
                metadata={"description": row["source_title"]},
            )
            for row in self.entries
        ]

    async def metadata(self, candidate):
        return candidate.metadata

    async def direct_media(self, candidate):
        return MediaRef(f"https://cdn.example.org/{candidate.provider_id}.mp4", "video/mp4")


class NoEncoder:
    def encode_text(self, text):
        return np.ones(512, dtype=np.float32)


def translate(text, src, dst):
    return f"{text} {dst}"


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg/ffprobe required for synthetic videos")
    root = tmp_path_factory.mktemp("m28-import-sources")
    for number, name in enumerate(("rain1", "rain2")):
        output = root / f"{name}.mp4"
        video_filter = "testsrc2=size=720x1280:rate=15"
        if number:
            video_filter += ",hue=h=110"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                video_filter,
                "-t",
                "8",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ],
            timeout=180,
            check=True,
            capture_output=True,
        )
    return root


@pytest.fixture
def engine(monkeypatch):
    # Use actual v2 ingest, FFmpeg normalization, metadata, asset verification.
    # Only optional expensive semantic window-ranker is deterministic and fake.
    import clipoteca.ingest as module

    monkeypatch.setattr(
        module,
        "_candidate_windows",
        lambda *args, **kwargs: [SourceWindow(0.0, 5.0)],
    )


def _import(job, out, catalog, videos, *, entries=None, **kwargs):
    async def downloader(url, root):
        ident = url.rsplit("/", 1)[-1].removesuffix(".mp4")
        target = root / f"{ident}.mp4"
        shutil.copyfile(videos / f"{ident}.mp4", target)
        return target

    active = [FakeAdapter("pexels", entries), FakeAdapter("pixabay", entries)]
    return import_scene(
        job,
        out,
        catalog_path=catalog,
        adapters=active,
        downloader=downloader,
        translator=translate,
        embedding_encoder=NoEncoder(),
        relevance_check=lambda record, path: True,
        **kwargs,
    )


def _catalog(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(load("catalogo.json")), encoding="utf-8")
    return path


def test_happy_two_real_ffmpeg_imports_and_manifest(tmp_path, videos, engine):
    out = tmp_path / "import"
    result = _import(load("job.json"), out, _catalog(tmp_path), videos, max_results=2)
    assert result["status"] == "ok", result
    assert len(result["clips"]) == 2, result
    assert result["termination_reason"] == "max_results"
    assert result["queries_tried"] == ["rain movement"]
    manifest = json.loads((out / "imported.json").read_text())
    assert manifest == result
    assignment = json.loads((out / "assignment.json").read_text())
    assert assignment["status"] == "imported"
    assert assignment["primary"]["clip_id"].startswith("imp-")
    assert "asset_url" not in assignment["primary"]
    assert len(assignment["backups"]) == 1
    for clip in result["clips"]:
        assert clip["imp_id"] == "imp-" + clip["sha256"][:12]
        assert not Path(clip["local_path"]).is_absolute()
        assert not any(".." in p for p in Path(clip["local_path"]).parts)
        video = out / clip["local_path"]
        assert video.is_file()
        assert hashlib.sha256(video.read_bytes()).hexdigest() == clip["sha256"]
        assert video.stat().st_size == clip["size_bytes"]
        assert (out / clip["assets"]["thumbnail"]).is_file()
        assert all((out / p).is_file() for p in clip["assets"]["storyboard"])
        assert clip["record"]["id"].startswith("clip-")
        assert clip["record"]["storage"]["asset_url"].startswith("local://")
    assert not (out / ".ingest-local").exists()


def test_no_remote_clients_or_persistence_and_env_restoration(
    tmp_path,
    videos,
    engine,
    monkeypatch,
):
    import clipoteca.ingest as ing

    touched = []

    def forbidden(*args, **kwargs):
        touched.append(1)
        raise AssertionError("remote writes forbidden")

    for name in (
        "GitHubCatalogWriter",
        "GitHubReleaseStorage",
        "GitHubJobWriter",
        "GitHubEmbeddingWriter",
        "GitHubQueries",
        "GitHubShards",
    ):
        monkeypatch.setattr(ing, name, forbidden)
    monkeypatch.setenv("CLIPOTECA_PERSIST_JOBS", "1")
    monkeypatch.setenv("CLIPOTECA_M21_SIDECARS", "1")
    expected = dict(os.environ)
    out = tmp_path / "local"
    result = _import(load("job.json"), out, _catalog(tmp_path), videos, max_results=1)
    assert result["status"] == "ok", result
    assert touched == []
    assert dict(os.environ) == expected
    assert (out / "imported.json").exists()


def test_published_source_not_reimported(tmp_path, videos, engine):
    job = load("job.json")
    catalog = _catalog(tmp_path)
    first = _import(job, tmp_path / "first", catalog, videos, max_results=2)
    catalog.write_text(json.dumps([first["clips"][0]["record"]]), encoding="utf-8")
    next_result = _import(job, tmp_path / "next", catalog, videos, max_results=2)
    digests = {row["sha256"] for row in next_result["clips"]}
    assert first["clips"][0]["sha256"] not in digests


def test_no_results_and_cli_exit_three(tmp_path, monkeypatch, capsys):
    job = load("job.json")
    empty = tmp_path / "none"
    catalog = _catalog(tmp_path)
    result = import_scene(
        job,
        empty,
        catalog_path=catalog,
        adapters=[],
        translator=translate,
        embedding_encoder=NoEncoder(),
    )
    assert result["status"] == "no_results"
    assert result["termination_reason"] == "exhausted"
    assert result["clips"] == []
    assert json.loads((empty / "assignment.json").read_text())["status"] == "needs_import"
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    monkeypatch.setattr(importer, "_adapters", lambda providers: [])
    assert (
        importer.main(
            [
                "scene",
                "--job",
                str(job_path),
                "--catalog",
                str(catalog),
                "--out",
                str(tmp_path / "cli"),
                "--max-results",
                "1",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["imported"] == 0


def _assert_import_integrity(report, out):
    for clip in report["clips"]:
        data = (out / clip["assets"]["video"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert clip["sha256"] == digest
        assert clip["size_bytes"] == len(data)
        assert clip["imp_id"] == "imp-" + digest[:12]
        assert clip["record"]["media"]["sha256"] == digest
        assert clip["record"]["media"]["size_bytes"] == len(data)


def _stable_import_report(report):
    # FFmpeg may encode equivalent frames with different container bytes.
    # Only byte-dependent metadata/paths and elapsed time are normalized.
    result = copy.deepcopy(report)
    result.pop("time_s", None)
    for clip in result["clips"]:
        ident = clip["imp_id"]
        for key in ("sha256", "size_bytes"):
            clip.pop(key)
            clip["record"]["media"].pop(key)
        clip["imp_id"] = "imp-content"
        clip["local_path"] = clip["local_path"].replace(ident, "imp-content")
        for key, value in clip["assets"].items():
            if isinstance(value, list):
                clip["assets"][key] = [path.replace(ident, "imp-content") for path in value]
            else:
                clip["assets"][key] = value.replace(ident, "imp-content")
    return result


def test_determinism_and_clean_reexecution(tmp_path, videos, engine):
    out = tmp_path / "again"
    catalog = _catalog(tmp_path)
    a = _import(load("job.json"), out, catalog, videos, max_results=1)
    _assert_import_integrity(a, out)
    (out / "clips" / "orphan.mp4").write_bytes(b"garbage")
    b = _import(load("job.json"), out, catalog, videos, max_results=1)
    assert not (out / "clips" / "orphan.mp4").exists()
    _assert_import_integrity(b, out)
    assert _stable_import_report(a) == _stable_import_report(b)
    for name in ("clips", "thumbs", "storyboards"):
        assert not any(p.is_symlink() for p in (out / name).rglob("*"))


def test_timeout_keeps_already_imported(tmp_path, monkeypatch):
    report = load("job.json")
    catalog = _catalog(tmp_path)

    def fake_record():
        return {
            "id": "clip-000001",
            "source": {"source_url": "https://example.org/rain", "provider": "pexels"},
            "title": {"en": "Rain", "es": "Rain"},
            "description": {"en": "Rain", "es": "Rain"},
            "tags": {"en": ["rain"], "es": ["rain"]},
            "media": {
                "sha256": hashlib.sha256(b"some bytes").hexdigest(),
                "size_bytes": 10,
                "duration_seconds": 5,
                "width": 720,
                "height": 1280,
            },
            "storage": {"asset_url": "local://unused"},
            "created_at": "now",
            "status": "published",
        }

    call = 0

    async def fake_ingest(query, adapters, **kwargs):
        nonlocal call
        call += 1
        if call == 1:
            rec = fake_record()
            storage = kwargs["storage"]
            from clipoteca.storage.github import ReleaseBatch

            release = ReleaseBatch(1, "studio-local", "local://uploads")
            # Bypass media normalization only in this timeout accounting test.
            temp = tmp_path / "temp-video.mp4"
            temp.write_bytes(b"some bytes")
            thumb = tmp_path / "thumb.webp"
            thumb.write_bytes(b"thumb")
            vid = storage.upload_asset(release, temp, "clip_clip-000001.mp4")
            t = storage.upload_asset(release, thumb, "thumb_clip-000001.webp")
            kwargs["writer"].update([rec], verified_assets={"clip-000001": (vid, t)})
            return IngestReport("fake", "published", ("clip-000001",), ())
        await __import__("asyncio").sleep(5)
        return IngestReport("fake", "no_results", (), ())

    monkeypatch.setattr(importer, "ingest", fake_ingest)
    result = import_scene(
        report,
        tmp_path / "timeout",
        catalog_path=catalog,
        adapters=[FakeAdapter("pexels")],
        timeout_s=0.3,
        max_results=2,
        relevance_check=lambda record, path: True,
    )
    assert result["status"] == "ok"
    assert "timeout" in result["errors"]
    assert result["termination_reason"] == "timeout"
    assert len(result["clips"]) == 1
    assert not (tmp_path / "timeout" / ".ingest-local").exists()


def test_bad_job_types_and_cli_error(tmp_path):
    catalog = _catalog(tmp_path)
    for bad in ("../oops", "scene/x", "", 1):
        job = {**load("job.json"), "id": bad}
        with pytest.raises(ImportSceneError):
            import_scene(job, tmp_path / "invalid", catalog_path=catalog, adapters=[])
    assert not (tmp_path / "invalid").exists()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_imported_assignment_runs_clipscene_and_local_path_attacks(
    tmp_path,
    videos,
    engine,
):
    pytest.importorskip("cv2")
    from test_clipscene_m28 import FakeScorer

    job = load("job.json")
    catalog = _catalog(tmp_path)
    out = tmp_path / "imported"
    result = _import(job, out, catalog, videos, max_results=2)
    assignment = json.loads((out / "assignment.json").read_text())
    assert result["status"] == "ok", result
    report = clip_scene(
        job,
        assignment,
        tmp_path / "render",
        local_root=out,
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0.1),
    )
    assert report["status"] == "ok", report
    assert report["clip"]["imported"] is True
    assert report["clip"]["local_path"] == assignment["primary"]["local_path"]
    assert "asset_url" not in report["clip"]
    primary, backup = assignment["primary"], assignment["backups"][0]
    changed = copy.deepcopy(assignment)
    changed["primary"]["sha256"] = "0" * 64
    report = clip_scene(
        job,
        changed,
        tmp_path / "backup",
        local_root=out,
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0.1),
    )
    assert report["status"] == "ok", report
    assert report["used"] == "backup"
    assert report["tried"][0]["reason"] == "hash_mismatch"
    for bad in ("../escape.mp4", "/tmp/outside.mp4", "clips/../escape.mp4"):
        changed = {**assignment, "primary": {**primary, "local_path": bad}, "backups": []}
        report = clip_scene(
            job,
            changed,
            tmp_path / "bad",
            local_root=out,
            scorer=FakeScorer(),
            ocr=lambda _: ([], 0.1),
        )
        assert report["status"] == "fail"
        assert report["tried"][0]["reason"] == "bad_local_path"
    link = out / "clips" / "symlink.mp4"
    link.symlink_to(out / backup["local_path"])
    changed = {
        **assignment,
        "primary": {**primary, "local_path": "clips/symlink.mp4"},
        "backups": [],
    }
    report = clip_scene(job, changed, tmp_path / "symlink", local_root=out)
    assert report["tried"][0]["reason"] == "bad_local_path"


def test_query_order_english_spanish_and_casefold_dedup(tmp_path, monkeypatch):
    job = load("job.json")
    job["queries"] = {
        "en": ["RAIN movement", "rain MOVEMENT"],
        "es": ["lluvia movimiento", "RAIN movement"],
    }
    invoked = []

    async def no_results(query, adapters, **kwargs):
        invoked.append((query, kwargs["horizon"], kwargs["filters"]))
        return IngestReport("fixture", "no_results", (), ())

    monkeypatch.setattr(importer, "ingest", no_results)
    result = import_scene(
        job,
        tmp_path / "query-order",
        catalog_path=_catalog(tmp_path),
        adapters=[FakeAdapter("pexels")],
        translator=translate,
    )
    assert result["status"] == "no_results"
    assert result["queries_tried"] == ["RAIN movement", "lluvia movimiento"]
    assert [row[0] for row in invoked] == result["queries_tried"]
    assert [row[1] for row in invoked] == ["studio-v1-0", "studio-v1-1"]
    assert all(row[2]["max_duration"] == 8 for row in invoked)
    # Orchestrator review: clip-scene reframes any orientation, so the engine must not be asked
    # to discard horizontal sources for a vertical video (that left real runs with nothing).
    assert all("aspect_ratio" not in row[2] for row in invoked)


def _run_with_reports(tmp_path, monkeypatch, capsys, name, reports):
    """Drive import_scene and the CLI with canned engine reports (no media, no network)."""
    job = load("job.json")
    job["queries"] = {"en": ["rain"], "es": ["lluvia"]}
    queue = list(reports)

    async def canned(query, adapters, **kwargs):
        return queue.pop(0) if queue else IngestReport("fixture", "no_results", (), ())

    monkeypatch.setattr(importer, "ingest", canned)
    monkeypatch.setattr(importer, "_adapters", lambda providers: [FakeAdapter("pexels")])
    result = import_scene(
        job,
        tmp_path / name,
        catalog_path=_catalog(tmp_path),
        adapters=[FakeAdapter("pexels")],
        translator=translate,
    )
    queue[:] = list(reports)
    job_path = tmp_path / f"{name}.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    argv = ["scene", "--job", str(job_path), "--catalog", str(_catalog(tmp_path))]
    code = importer.main([*argv, "--out", str(tmp_path / f"{name}-cli")])
    capsys.readouterr()
    return result, code


def test_soft_rejections_are_no_results_not_errors(tmp_path, monkeypatch, capsys):
    """Sonnet review: filtered or duplicate candidates made the run an "error" (exit 1)."""
    reports = [
        IngestReport(
            "a",
            "unavailable",
            (),
            ("pexels:1:aspect_filter", "pexels:2:search_only", "pexels:3:media_filter"),
        ),
        IngestReport(
            "b",
            "duplicate",
            ("clip-000001",),
            ("pexels:4:duplicate:clip-000001:sha256", "pexels:5:scene_quality_rejected"),
        ),
    ]
    result, code = _run_with_reports(tmp_path, monkeypatch, capsys, "soft", reports)
    assert result["status"] == "no_results" and code == 3
    assert result["errors"] == []
    assert result["rejections"] == {
        "aspect_filter": 1,
        "duplicate": 1,
        "media_filter": 1,
        "scene_quality_rejected": 1,
        "search_only": 1,
    }
    assignment = json.loads((tmp_path / "soft" / "assignment.json").read_text())
    assert assignment["status"] == "needs_import"


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (IngestReport("a", "failed", (), ("pexels:1:download_error",)), "download_error"),
        (IngestReport("a", "unavailable", (), ()), "ingest_unavailable"),
        (IngestReport("a", "rate_limited", (), ("pexels:1:rate_limited",)), "rate_limited"),
        (
            IngestReport("a", "failed", (), ("pexels:1:aspect_filter", "pexels:2:ffmpeg_failed")),
            "ffmpeg_failed",
        ),
        (
            IngestReport("a", "failed", (), ("pexels:1:https://example.org/secret?token=x",)),
            "ingest_error",
        ),
    ],
)
def test_real_failures_stay_errors_without_leaking_payloads(
    tmp_path, monkeypatch, capsys, report, expected
):
    result, code = _run_with_reports(tmp_path, monkeypatch, capsys, "hard", [report])
    assert result["status"] == "error" and code == 1
    assert expected in result["errors"]
    assert "example.org" not in json.dumps(result)


def test_spread_queries_does_not_reserve_budget(tmp_path, monkeypatch):
    job = load("job.json")
    job["queries"] = {"en": ["rain", "storm"], "es": ["lluvia", "tormenta"]}
    calls = []

    async def fake(query, adapters, **kwargs):
        calls.append((query, kwargs["max_results"]))
        return IngestReport("fixture", "no_results", (), ())

    monkeypatch.setattr(importer, "ingest", fake)
    report = import_scene(
        job,
        tmp_path / "spread",
        catalog_path=_catalog(tmp_path),
        adapters=[FakeAdapter("pexels")],
        max_results=8,
        spread_queries=True,
    )
    assert report["queries_tried"] == ["rain", "storm", "lluvia", "tormenta"]
    assert calls[0] == ("rain", 8)
    assert len(calls) == 4


def test_simple_queries_remove_common_qualifiers():
    job = {"title": "La lona", "prompt": "Real cinematic footage of waterproof tarpaulin"}
    assert importer.simple_queries(job, ["rain"]) == ["lona", "waterproof tarpaulin"]
    assert importer.simple_queries(job, ["lona", "waterproof tarpaulin"]) == ["waterproof"]


@pytest.mark.parametrize("raises", [False, True])
def test_wall_deadline_restores_handler_and_timer(raises):
    import signal

    previous = signal.getsignal(signal.SIGALRM)
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)
    try:
        with importer._wall_deadline(60):
            assert signal.getsignal(signal.SIGALRM) != previous
            assert signal.getitimer(signal.ITIMER_REAL)[0] > 0
            if raises:
                raise RuntimeError("body failed")
    except RuntimeError:
        assert raises
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)


def test_wall_deadline_preserves_external_timer_and_thread():
    import signal
    import threading

    previous = signal.getsignal(signal.SIGALRM)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with importer._wall_deadline(1):
            assert signal.getsignal(signal.SIGALRM) == previous
            assert signal.getitimer(signal.ITIMER_REAL)[0] > 10
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    observed = []

    def thread():
        with importer._wall_deadline(10):
            observed.append(signal.getitimer(signal.ITIMER_REAL))

    worker = threading.Thread(target=thread)
    worker.start()
    worker.join()
    assert observed == [(0, 0)]
    assert signal.getsignal(signal.SIGALRM) == previous


def test_assignment_name_prefers_rapa_nui_over_nui_cui():
    def item(title, ident):
        return {
            "imp_id": ident,
            "duration": 8,
            "width": 1080,
            "height": 1920,
            "sha256": "a" * 64,
            "size_bytes": 123,
            "source_url": "https://example.org",
            "local_path": f"clips/{ident}.mp4",
            "record": {"title": {"en": title}},
        }

    job = load("job.json")
    job["len_est"] = 5
    job["queries"] = {"es": ["moais Rapa Nui"], "en": ["moai Rapa Nui"]}
    result = importer._assignment(
        job, [item("Moai Nui Cui statue", "wrong"), item("Rapa Nui moai", "right")]
    )
    assert result["primary"]["clip_id"] == "right"
    assert result["primary"]["name_match"]
    assert not result["backups"][0]["name_match"]


def test_irrelevant_candidate_is_never_downloaded(tmp_path, videos, engine):
    entries = load("candidatos.json")
    entries.insert(
        0,
        {
            "provider": "pexels",
            "provider_id": "irrelevant",
            "source_title": "sports car",
            "source_url": "https://pexels.example.org/source/irrelevant",
        },
    )
    result = _import(
        load("job.json"),
        tmp_path / "gate",
        _catalog(tmp_path),
        videos,
        entries=entries,
        max_results=2,
    )
    assert result["status"] == "ok"
    assert result["rejections"]["text_gate"] >= 1
    assert result["downloads_started"] == 2
    assert result["downloads_completed"] == 2
    assert result["text_evaluated"] >= 3
    assert result["evaluated_count"] >= 2
