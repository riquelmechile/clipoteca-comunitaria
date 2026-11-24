"""M32 publication contract: synthetic bytes and local or mocked GitHub APIs."""

import hashlib
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from clipoteca.catalog import Catalog
from clipoteca.studio import publish
from clipoteca.studio.script import parse_video_script, script_id

DAY = date(2026, 1, 2)
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TOKEN = "sentinel-private-credential-4321"
REPO = "example/clipoteca"


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def clip_record(index, content):
    text = {"es": f"Clip {index}", "en": f"Clip {index}"}
    return {
        "id": f"clip-{index:06d}",
        "title": text,
        "description": text,
        "tags": {"es": [], "en": []},
        "source": {
            "provider": "test",
            "provider_id": str(index),
            "source_language": "en",
            "source_title": f"Source {index}",
            "source_url": f"https://example.invalid/source/{index}",
        },
        "media": {
            "duration_seconds": 4,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "aspect_ratio": "16:9",
            "orientation": "horizontal",
            "codec": "h264",
            "container": "mp4",
            "has_audio": False,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "perceptual_hash": str(index),
        },
        "storage": {
            "provider": "github_releases",
            "asset_url": "local://video",
            "thumbnail_url": "local://thumb",
            "storyboard_urls": ["local://story"],
        },
        "status": "published",
        "created_at": "1970-01-01T00:00:00Z",
    }


def imported(root, index):
    content = f"clip bytes {index}".encode()
    record = clip_record(index, content)
    digest = record["media"]["sha256"]
    identifier = "imp-" + digest[:12]
    assets = {
        "video": f"clips/{identifier}.mp4",
        "thumbnail": f"thumbs/{identifier}.webp",
        "storyboard": [f"storyboards/{identifier}_00.webp"],
    }
    for relative, data in (
        (assets["video"], content),
        (assets["thumbnail"], b"thumbnail"),
        (assets["storyboard"][0], b"storyboard"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {
        "imp_id": identifier,
        "sha256": digest,
        "size_bytes": len(content),
        "local_path": assets["video"],
        "source_url": record["source"]["source_url"],
        "assets": assets,
        "record": record,
    }


def catalogue_record(index):
    record = clip_record(index, f"catalogue bytes {index}".encode())
    record["storage"] = {
        "provider": "github_releases",
        "asset_url": f"https://github.com/example/clipoteca/releases/download/batch/clip_{index}",
        "thumbnail_url": "https://example.invalid/thumb",
    }
    return record


def make_work(work, video=b"finished video"):
    script = {
        "title": "Four scenes",
        "aspect": "16:9",
        "scenes": [
            {
                "id": f"s{index}",
                "type": "fact",
                "title": "Scene",
                "say": "Una escena",
                "query": {"es": ["agua"], "en": ["water"]},
                "prompt": "water",
            }
            for index in range(1, 5)
        ],
    }
    put(work / "guion.json", script)
    put(work / "timeline.json", {"total": 16})
    (work / "video.mp4").write_bytes(video)
    put(
        work / "qa_report.json",
        {
            "pass": True,
            "failed": [],
            "script_id": script_id(parse_video_script(script)),
            "video": {"size_bytes": len(video), "sha256": hashlib.sha256(video).hexdigest()},
            "checks": {
                "whisper_wer": {"pass": True, "wer": 0},
                "subtitle_sync": {"pass": True, "mean_ms": 10, "p95_ms": 20},
                "loudness_ebur128": {"pass": True, "lufs": -14, "true_peak_dbfs": -1},
            },
        },
    )
    put(
        work / "render.json",
        {
            "duration_s": 16,
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "size_bytes": len(video),
            "render_s": 2,
            "mux_s": 1,
            "hyperframes": "0.9.36",
            "gsap_sha256": "a" * 64,
        },
    )
    put(work / "timings.json", {"plan": 1})
    selected = []
    for index in range(1, 5):
        root = work / "scenes" / f"s{index}"
        if index <= 2:
            record = catalogue_record(index)
            clip = {"clip_id": record["id"], "source_url": record["source"]["source_url"]}
        else:
            entry = imported(root / "import", index)
            unused = imported(root / "import", index + 10)
            put(root / "import" / "imported.json", {"clips": [entry, unused]})
            selected.append(entry)
            clip = {
                "clip_id": entry["imp_id"],
                "imported": True,
                "local_path": entry["local_path"],
                "source_url": entry["source_url"],
            }
        put(root / "result.json", {"status": "ok", "clip": {**clip, "start": 0, "len": 3}})
    return selected


class Writer(publish.DryWriter):
    def __init__(self, root):
        super().__init__(root, Catalog([catalogue_record(1), catalogue_record(2)]))
        self.updates = []

    def update(self, incoming, *, verified_assets):
        self.updates.append((incoming, verified_assets))
        return super().update(incoming, verified_assets=verified_assets)


class Storage(publish.DryStorage):
    def reserved_ids(self, release):
        return super().reserved_ids(release) | {"clip-000003"}


@pytest.fixture
def setup(tmp_path):
    work = tmp_path / "work"
    root = tmp_path / "output"
    selected = make_work(work)
    storage = Storage(root)
    writer = Writer(root)
    records = publish.DryRecords(root)
    return work, root, selected, storage, writer, records


def run(setup):
    work, _, _, storage, writer, records = setup
    return publish.publish_video(
        work,
        repository=REPO,
        token=TOKEN,
        storage=storage,
        writer=writer,
        records=records,
        today=DAY,
        now=NOW,
    )


def test_happy_path_and_unused(setup):
    work, root, selected, _, writer, _ = setup
    result = run(setup)
    assert result["video_id"] == "video-2026-01-02-001"
    assert result["clips"] == {
        selected[0]["imp_id"]: "clip-000004",
        selected[1]["imp_id"]: "clip-000005",
    }
    assert result["reused_clips"] == []
    assert result["already_published"] is False
    assert len(writer.updates) == 1
    incoming, verified = writer.updates[0]
    assert len(incoming) == 2
    assert all(len(assets) == 3 for assets in verified.values())
    for record in incoming:
        assert record["created_at"] == "2026-01-02T03:04:05Z"
        assert "local://" not in json.dumps(record)
        assert "imp-" not in json.dumps(record)
    record = publish._read(root / result["record_path"])
    publish._validator("video-record.schema.json").validate(record)
    assert record["script"] == publish._read(work / "guion.json")
    assert [row["clip_id"] for row in record["scenes"]] == [
        "clip-000001",
        "clip-000002",
        "clip-000004",
        "clip-000005",
    ]
    assert record["timings"] == {"plan": 1, "render_s": 2, "mux_s": 1}
    assert result == publish._read(work / "publish.json")
    assert len(list((root / "assets").rglob("*.mp4"))) == 3
    assert "local://" not in json.dumps(record)
    assert "imp-" not in json.dumps(record)


class NoCalls:
    def __getattr__(self, name):
        pytest.fail(f"unexpected remote access: {name}")


def reject(work, code):
    with pytest.raises(publish.PublishError) as exc:
        publish.publish_video(
            work,
            repository=REPO,
            token=TOKEN,
            storage=NoCalls(),
            writer=NoCalls(),
            records=NoCalls(),
        )
    assert exc.value.code == code


@pytest.mark.parametrize("change", ["pass", "sha", "script", "scene", "missing", "size"])
def test_gate(setup, change):
    work = setup[0]
    path = work / "qa_report.json"
    qa = publish._read(path)
    if change == "pass":
        qa["pass"] = False
    elif change == "sha":
        (work / "video.mp4").write_bytes(b"changed bytes!!")
    elif change == "script":
        qa["script_id"] = "b" * 64
    elif change == "scene":
        put(work / "scenes/s1/result.json", {"status": "no_candidate"})
    elif change == "size":
        render = publish._read(work / "render.json")
        render["size_bytes"] += 1
        put(work / "render.json", render)
    if change == "missing":
        path.unlink()
    else:
        put(path, qa)
    reject(work, 4)
    assert publish.main(["--work", str(work), "--dry-run", str(work / "dry")]) == 4


@pytest.mark.parametrize("change", ["absolute", "traversal", "symlink", "sha", "story", "empty"])
def test_import_validation(setup, change):
    work = setup[0]
    root = work / "scenes/s3/import"
    path = root / "imported.json"
    manifest = publish._read(path)
    entry = manifest["clips"][0]
    asset = root / entry["assets"]["video"]
    if change == "absolute":
        entry["assets"]["thumbnail"] = str(asset)
    elif change == "traversal":
        entry["assets"]["thumbnail"] = "../outside.webp"
    elif change == "symlink":
        thumb = root / entry["assets"]["thumbnail"]
        thumb.unlink()
        thumb.symlink_to(asset)
    elif change == "sha":
        asset.write_bytes(b"bad")
    elif change == "story":
        entry["assets"]["storyboard"] = []
    else:
        (root / entry["assets"]["thumbnail"]).write_bytes(b"")
    put(path, manifest)
    reject(work, 2)


def test_reuse_sha(setup):
    _, _, selected, _, writer, _ = setup
    existing = catalogue_record(6)
    existing["media"]["sha256"] = selected[0]["sha256"]
    writer.catalog.merge([existing])
    result = run(setup)
    assert result["clips"][selected[0]["imp_id"]] == "clip-000006"
    assert result["reused_clips"] == ["clip-000006"]
    assert len(writer.updates[0][0]) == 1


def test_rollback(setup, monkeypatch):
    _, root, _, _, writer, _ = setup

    def fail(*args, **kwargs):
        raise RuntimeError(f"{TOKEN} {REPO}")

    monkeypatch.setattr(writer, "update", fail)
    with pytest.raises(publish.PublishError, match="remote publication failed") as exc:
        run(setup)
    assert exc.value.code == 1
    assert TOKEN not in str(exc.value)
    assert REPO not in str(exc.value)
    assert not list((root / "assets").rglob("*.mp4"))
    assert not list((root / "assets").rglob("*.webp"))
    assert not list((root / "assets").glob("video-*"))
    assert not list((root / "data/videos").glob("*.json"))


@pytest.mark.parametrize("cut", ["catalogue", "release", "record"])
def test_resume(setup, monkeypatch, cut):
    _, root, _, storage, writer, records = setup

    def fail(*args, **kwargs):
        raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        if cut == "catalogue":
            patch.setattr(storage, "video_release", fail)
        elif cut == "release":
            patch.setattr(records, "save", fail)
        else:
            patch.setattr(publish, "_finish", fail)
        with pytest.raises(publish.PublishError):
            run(setup)
    result = run(setup)
    assert result["already_published"] == (cut == "record")
    assert result["video_id"] == "video-2026-01-02-001"
    assert len(writer.updates) == 1
    assert len(list((root / "assets").rglob("*.mp4"))) == 3
    assert len(list((root / "data/videos").glob("*.json"))) == 1
    assert run(setup)["already_published"] is True


def test_second_video_same_day(setup):
    first = run(setup)
    make_work(setup[0], b"another finished video")
    second = run(setup)
    assert first["video_id"] == "video-2026-01-02-001"
    assert second["video_id"] == "video-2026-01-02-002"
    assert second["clips"] == first["clips"]
    assert not second["already_published"]


def test_release_conflict():
    tags = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        payload = json.loads(request.content)
        tags.append(payload["tag_name"])
        assert payload["make_latest"] == "false"
        assert not payload["draft"] and not payload["prerelease"]
        if len(tags) == 1:
            return httpx.Response(422)
        return httpx.Response(
            201,
            json={
                "id": 7,
                "tag_name": tags[-1],
                "upload_url": "https://uploads.github.com/example",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        storage = publish.VideoStorage(REPO, client=client)
        release = storage.video_release(DAY, 10, "a" * 64)
    assert tags == ["video-2026-01-02-001", "video-2026-01-02-002"]
    assert release.tag == tags[-1]


def test_dry_run(setup, monkeypatch, capsys):
    work, root, _, _, _, _ = setup

    def forbidden(*args, **kwargs):
        pytest.fail("GitHub client instantiated in dry-run")

    monkeypatch.setattr(publish.httpx, "Client", forbidden)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    assert publish.main(["--work", str(work), "--dry-run", str(root)]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    record = (root / result["record_path"]).read_text()
    output = captured.out + captured.err + (work / "publish.json").read_text() + record
    assert TOKEN not in output
    assert REPO not in output.replace(f"https://github.com/{REPO}/releases/download/", "")
    assert len(publish._read(root / "data/clips.json")) == 4
