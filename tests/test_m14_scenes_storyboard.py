"""M14 offline real FFmpeg: bounded deterministic cuts, thumbnails and WebP storyboards."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from clipoteca.catalog import Catalog
from clipoteca.media.normalize import normalize_media
from clipoteca.media.probe import probe_media
from clipoteca.media.select import (
    FRAME_SIZE,
    SceneQualityRejected,
    SceneSelectionTimeout,
    SourceWindow,
    _scores,
    fallback_quality_window,
    rank_scene_windows,
)
from clipoteca.media.storyboard import create_storyboard
from clipoteca.media.thumbnail import create_thumbnail


@pytest.fixture(scope="module")
def scene_source(tmp_path_factory):
    target = tmp_path_factory.mktemp("m14-video") / "scenes.mp4"
    # 8s bright/static title card, 7s black fade, then 3 moving takes.
    inputs = [
        "color=c=white:s=320x180:r=12:d=8",
        "color=c=black:s=320x180:r=12:d=7",
        "testsrc2=s=320x180:r=12:d=15",
        "testsrc2=s=320x180:r=12:d=15, hue=h=80",
        "testsrc2=s=320x180:r=12:d=15, hue=h=150",
    ]
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for source in inputs:
        cmd.extend(["-f", "lavfi", "-i", source])
    concat = "".join(f"[{i}:v]" for i in range(len(inputs))) + (
        f"concat=n={len(inputs)}:v=1:a=0[v]"
    )
    cmd.extend(
        [
            "-filter_complex",
            concat,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            str(target),
        ]
    )
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return target


def test_60s_excludes_black_and_static_title(scene_source):
    windows = rank_scene_windows(scene_source, max_clips=2)
    assert len(windows) == 2
    assert all(window.start >= 15 for window in windows)
    assert all(1.5 <= window.duration <= 20 for window in windows)
    assert windows[0].end <= windows[1].start or windows[1].end <= windows[0].start
    assert windows == rank_scene_windows(scene_source, max_clips=2)


def test_short_video_keeps_start_and_duration(tmp_path):
    source = tmp_path / "short.mp4"
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
            "testsrc2=s=320x180:r=12:d=8",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=45,
    )
    window = rank_scene_windows(source)[0]
    assert window.start == 0
    assert window.end == pytest.approx(8, abs=0.1)


def test_single_take_45s_selects_valid_window(tmp_path):
    source = tmp_path / "take.mp4"
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
            "testsrc2=s=320x180:r=12:d=45",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    windows = rank_scene_windows(source, max_clips=2)
    assert windows
    assert all(0 <= w.start < w.end <= 45.1 and w.duration <= 20 for w in windows)


def test_frame_accurate_normalize_midpoint_and_storyboard(scene_source, tmp_path, monkeypatch):
    window = rank_scene_windows(scene_source, max_clips=1)[0]
    normalized = normalize_media(
        scene_source,
        tmp_path / "clip.mp4",
        start_seconds=window.start,
        max_duration_seconds=window.duration,
        threads=1,
    )
    assert normalized.media.duration_seconds <= 20
    assert normalized.media.codec == "h264"
    assert normalized.media.pix_fmt == "yuv420p"

    thumb = create_thumbnail(normalized.path, tmp_path / "thumb.webp")
    assert thumb.is_file() and thumb.stat().st_size > 0
    previews = create_storyboard(normalized.path, tmp_path / "previews")
    assert len(previews) == 6
    assert len({path.read_bytes() for path in previews}) >= 2
    for file in previews:
        info = probe_media(file)
        assert info.width == 320
        assert info.height > 0 and info.height % 2 == 0
        assert info.codec == "webp"


def test_half_second_clip_has_six_nonempty_preview_frames(tmp_path):
    source = tmp_path / "short-half.mp4"
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
            "testsrc2=s=320x180:r=12:d=0.5",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=45,
    )
    previews = create_storyboard(source, tmp_path / "frames")
    assert len(previews) == 6
    assert all(file.stat().st_size > 0 for file in previews)


def test_schema_keeps_old_clips_valid_and_distinguishes_source_windows():
    schema = json.loads(Path("schemas/clip.schema.json").read_text())
    validator = Draft202012Validator(schema)
    from test_catalog_m1 import sample_clip

    old = sample_clip()
    validator.validate(old)
    first = json.loads(json.dumps(old))
    first["source"].update({"source_start": 15, "source_end": 35})
    first["storage"]["storyboard_urls"] = [
        f"https://github.com/example/story-{i}.webp" for i in range(6)
    ]
    validator.validate(first)
    second = json.loads(json.dumps(first))
    second["source"].update({"source_start": 35, "source_end": 55})
    second["media"]["sha256"] = "b" * 64
    second["media"]["perceptual_hash"] = "unique"
    catalog = Catalog([first])
    assert catalog.duplicate_match(second) is None
    # Similar frames from disjoint windows of the same origin are independent.
    second["media"]["perceptual_hash"] = first["media"]["perceptual_hash"]
    assert catalog.duplicate_match(second, phash_similar=lambda _a, _b: True) is None
    cross_source = json.loads(json.dumps(second))
    cross_source["source"]["provider_id"] = "another-video"
    cross_source["source"]["source_url"] = "https://example.test/another-video"
    assert catalog.duplicate_match(cross_source, phash_similar=lambda _a, _b: True)
    overlap = json.loads(json.dumps(second))
    overlap["source"]["source_start"] = 30
    assert catalog.duplicate_match(overlap) is not None


def test_slow_textured_motion_scores_above_static_title():
    # High-edge static bars model a flat title card. The second textured frame
    # changes only one luma level: absolute motion is gentle but real.
    static = bytes((40 if (i // 8) % 2 else 210) for i in range(FRAME_SIZE))
    textured_a = bytes(70 + (i * 37) % 120 for i in range(FRAME_SIZE))
    textured_b = bytes(
        min(255, value + (1 if i % 5 == 0 else 0)) for i, value in enumerate(textured_a)
    )
    static_score = _scores(static + static)
    slow_score = _scores(textured_a + textured_b)
    assert max(static_score) <= 0.03
    assert min(slow_score) >= 0.12


def test_single_frame_title_is_rejected_but_real_texture_survives():
    static = bytes((40 if (i // 8) % 2 else 210) for i in range(FRAME_SIZE))
    textured = bytes(55 + (i * 37) % 170 for i in range(FRAME_SIZE))
    assert _scores(static) == [0.0]
    assert _scores(textured)[0] >= 0.12


def test_antialiased_drawtext_is_rejected_but_mandelbrot_survives(tmp_path):
    def render(name, source):
        target = tmp_path / name
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
                source,
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-preset",
                "ultrafast",
                str(target),
            ],
            check=True,
            capture_output=True,
            timeout=45,
        )
        return target

    # Static ffmpeg builds have no fontconfig: name a font file when the runner has one.
    font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    fontfile = f"fontfile={font}:" if font.is_file() else ""
    try:
        title = render(
            "title.mp4",
            "color=c=gray:s=320x180:r=12,"
            f"drawtext={fontfile}text='TITLE':fontcolor=white:fontsize=54:borderw=2:"
            "bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2",
        )
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"ffmpeg cannot draw text here: {exc.stderr.strip()[-160:]}")
    texture = render("texture.mp4", "mandelbrot=size=320x180:rate=12")
    with pytest.raises(SceneQualityRejected, match="minimum quality"):
        rank_scene_windows(title)
    assert rank_scene_windows(texture)[0].duration == pytest.approx(1, abs=0.1)


def test_short_high_quality_tail_cannot_be_primary(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from clipoteca.media.scenes import Scene

    monkeypatch.setattr(
        "clipoteca.media.select.probe_media",
        lambda *_args, **_kwargs: SimpleNamespace(duration_seconds=23.0),
    )
    monkeypatch.setattr(
        "clipoteca.media.select._sample_scores", lambda *_args, **_kwargs: [0.2] * 20 + [1.0] * 3
    )
    monkeypatch.setattr(
        "clipoteca.media.select.detect_scenes", lambda *_args, **_kwargs: [Scene(0, 23)]
    )
    selected = rank_scene_windows(tmp_path / "unused.mp4", max_clips=1)
    assert selected[0].duration >= 8
    assert selected[0].start < 16


def test_max_duration_below_eight_keeps_valid_windows(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "clipoteca.media.select.probe_media",
        lambda *_args, **_kwargs: SimpleNamespace(duration_seconds=30.0),
    )
    monkeypatch.setattr(
        "clipoteca.media.select._sample_scores", lambda *_args, **_kwargs: [0.8] * 30
    )
    monkeypatch.setattr("clipoteca.media.select.detect_scenes", lambda *_args, **_kwargs: [])
    selected = rank_scene_windows(tmp_path / "unused.mp4", max_duration=5, max_clips=2)
    assert len(selected) == 2
    assert all(window.duration == 5 for window in selected)


def test_timeout_fallback_skips_black_prefix_and_rejects_flat(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "clipoteca.media.select._sample_scores",
        lambda *_args, **_kwargs: [0.0] * 5 + [0.8] * 20,
    )
    assert fallback_quality_window(
        tmp_path / "unused.mp4", max_duration=20, total_duration=25
    ) == SourceWindow(5, 25)
    monkeypatch.setattr(
        "clipoteca.media.select._sample_scores", lambda *_args, **_kwargs: [0.0] * 25
    )
    with pytest.raises(SceneQualityRejected, match="fallback window"):
        fallback_quality_window(tmp_path / "unused.mp4", max_duration=20, total_duration=25)


def test_all_black_45s_is_rejected(tmp_path):
    source = tmp_path / "black.mp4"
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
            "color=c=black:s=320x180:r=12:d=45",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with pytest.raises(SceneQualityRejected, match="minimum quality"):
        rank_scene_windows(source, max_clips=2)


def test_sampling_timeout_uses_v1_leading_window_and_records_warning(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from clipoteca.ingest import _candidate_windows

    def timeout(*_args, **_kwargs):
        raise SceneSelectionTimeout("deadline")

    monkeypatch.setattr("clipoteca.ingest.rank_scene_windows", timeout)
    monkeypatch.setattr(
        "clipoteca.ingest.probe_media",
        lambda *_args, **_kwargs: SimpleNamespace(duration_seconds=75.0),
    )
    monkeypatch.setattr(
        "clipoteca.ingest.fallback_quality_window",
        lambda *_args, **_kwargs: SourceWindow(7, 27),
    )
    job = {}
    windows = _candidate_windows(
        tmp_path / "source.mp4",
        duration_limit=20,
        max_clips=2,
        job=job,
        origin_id="pexels:slow",
    )
    assert windows == [SourceWindow(7, 27)]
    assert job["source_warnings"]["pexels:slow"] == (
        "scene_selection_timeout:quality_checked_fallback:7.000"
    )


def test_sampling_timeout_rejects_when_bounded_fallback_also_times_out(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from clipoteca.ingest import _candidate_windows

    def timeout(*_args, **_kwargs):
        raise SceneSelectionTimeout("deadline")

    monkeypatch.setattr("clipoteca.ingest.rank_scene_windows", timeout)
    monkeypatch.setattr("clipoteca.ingest.fallback_quality_window", timeout)
    monkeypatch.setattr(
        "clipoteca.ingest.probe_media",
        lambda *_args, **_kwargs: SimpleNamespace(duration_seconds=75.0),
    )
    job = {}
    with pytest.raises(SceneQualityRejected, match="fallback quality sampling timed out"):
        _candidate_windows(
            tmp_path / "source.mp4",
            duration_limit=20,
            max_clips=2,
            job=job,
            origin_id="pexels:slow",
        )
    assert job["source_warnings"]["pexels:slow"] == (
        "scene_selection_timeout:fallback_timeout_rejected"
    )


def test_published_source_ranges_skip_before_download():
    from clipoteca.ingest import _published_source_complete

    clips = [
        {
            "source": {
                "source_url": "https://example.test/video?id=7",
                "source_start": 0,
                "source_end": 20,
            }
        },
        {
            "source": {
                "source_url": "https://example.test/video?id=7",
                "source_start": 20,
                "source_end": 40,
            }
        },
    ]
    matched = _published_source_complete(clips, "https://example.test/video?id=7", max_clips=2)
    assert matched is clips[0]
    assert (
        _published_source_complete(
            clips[:1],
            "https://example.test/video?id=7",
            2,
            candidate_duration=15,
        )
        is clips[0]
    )
    assert (
        _published_source_complete(
            clips[:1],
            "https://example.test/video?id=7",
            2,
            candidate_duration=0,
        )
        is clips[0]
    )
    assert (
        _published_source_complete(
            clips[:1],
            "https://example.test/video?id=7",
            2,
            candidate_duration=None,
        )
        is clips[0]
    )
    assert (
        _published_source_complete(
            clips[:1],
            "https://example.test/video?id=7",
            2,
            candidate_duration=25,
        )
        is clips[0]
    )
    assert (
        _published_source_complete(
            clips[:1],
            "https://example.test/video?id=7",
            2,
            candidate_duration=120,
        )
        is None
    )


@pytest.mark.asyncio
async def test_quality_rejected_source_is_quarantined_for_24_hours(monkeypatch, tmp_path):
    from clipoteca.catalog import Catalog
    from clipoteca.ingest import ingest
    from test_ingest_m4c import FixtureAdapter

    class EmptyWriter:
        def read_catalog(self):
            return Catalog([]), "empty"

    downloads = 0

    async def downloader(_url, destination):
        nonlocal downloads
        downloads += 1
        target = destination / "source.mp4"
        target.write_bytes(b"fixture")
        return target

    monkeypatch.setattr(
        "clipoteca.ingest.rank_scene_windows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SceneQualityRejected("quality floor")),
    )
    kwargs = {
        "repository": "riquelmechile/clipoteca-comunitaria",
        "token": "test",
        "writer": EmptyWriter(),
        "storage": object(),
        "downloader": downloader,
        "job_root": tmp_path / "jobs",
        "translator": lambda _text, _src, dst: {
            "es": "Lluvia",
            "en": "Rain",
            "zh": "雨",
            "ru": "Дождь",
        }[dst],
    }
    first = await ingest("rain", [FixtureAdapter("pexels")], **kwargs)
    second = await ingest("rain", [FixtureAdapter("pexels")], **kwargs)
    assert first.status == second.status == "dead_letter"
    assert downloads == 1

    job_path = next(
        path
        for path in (tmp_path / "jobs").glob("*.json")
        if not path.name.endswith(".metrics.json")
    )
    stored = json.loads(job_path.read_text())
    stored["updated_at"] = "2000-01-01T00:00:00Z"
    job_path.write_text(json.dumps(stored))
    third = await ingest("rain", [FixtureAdapter("pexels")], **kwargs)
    assert third.status == "dead_letter"
    assert downloads == 2


@pytest.mark.asyncio
async def test_real_long_source_publishes_two_independent_verified_clips(
    scene_source,
    tmp_path,
    monkeypatch,
):
    import base64
    import hashlib
    import shutil
    import httpx

    from clipoteca.ingest import ingest
    from clipoteca.catalog import GitHubCatalogWriter
    from test_ingest_m4c import FixtureAdapter
    from clipoteca.storage.github import ReleaseBatch, ReleaseAsset

    published: list[dict] = []
    generation = 0

    def github(request):
        nonlocal generation, published
        if request.method == "GET":
            encoded = base64.b64encode(json.dumps(published).encode()).decode()
            return httpx.Response(
                200,
                json={
                    "sha": f"rev-{generation}",
                    "encoding": "base64",
                    "content": encoded,
                },
            )
        payload = json.loads(request.content)
        assert payload["sha"] == f"rev-{generation}"
        published = json.loads(base64.b64decode(payload["content"]))
        generation += 1
        return httpx.Response(200, json={"commit": {"sha": f"commit-{generation}"}})

    class LocalAssets:
        def __init__(self):
            self.assets = {}

        def get_or_create_batch_release(self, _day):
            return ReleaseBatch(1, "batch", "https://uploads.github.test")

        def upload_asset(self, release, path, name):
            content = Path(path).read_bytes()
            asset = ReleaseAsset(
                len(self.assets) + 1,
                name,
                len(content),
                "uploaded",
                f"https://github.com/release/{name}",
            )
            self.assets[name] = (asset, hashlib.sha256(content).hexdigest())
            return asset

        def verify_asset(self, name, size, digest, release_id):
            assert release_id == 1
            asset, actual = self.assets[name]
            assert asset.size == size and digest == actual
            return asset

    storage = LocalAssets()
    writer = GitHubCatalogWriter(
        "riquelmechile/clipoteca-comunitaria",
        "test",
        client=httpx.Client(transport=httpx.MockTransport(github)),
    )

    async def downloader(_url, dest):
        target = dest / "source.mp4"
        shutil.copy(scene_source, target)
        return target

    # The content is still checked against duplicates; isolate hash collisions
    # of two synthetic testsrc shots so metadata/source-window semantics are clear.
    monkeypatch.setattr("clipoteca.ingest.perceptually_similar", lambda _a, _b: False)
    result = await ingest(
        "moving scenes",
        [FixtureAdapter("pexels")],
        repository="riquelmechile/clipoteca-comunitaria",
        token="test",
        max_results=2,
        max_clips_per_source=2,
        downloader=downloader,
        storage=storage,
        writer=writer,
        job_root=tmp_path / "jobs",
        translator=lambda txt, src, dst: {
            "es": "Escena",
            "en": "Scene",
            "zh": "场景",
            "ru": "Сцена",
        }[dst],
    )
    assert result.status == "published", result.errors
    assert len(result.published_ids) == len(published) == 2
    spans = [(c["source"]["source_start"], c["source"]["source_end"]) for c in published]
    assert spans[0][1] <= spans[1][0] or spans[1][1] <= spans[0][0]
    assert all(start >= 15 and end - start <= 20 for start, end in spans)
    assert all(len(c["storage"]["storyboard_urls"]) == 6 for c in published)
    assert len(storage.assets) == 16  # 2 clips * (MP4 + thumbnail + 6 frames).
