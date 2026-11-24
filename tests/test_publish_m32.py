"""M32 publication contract: synthetic bytes and local or mocked GitHub APIs."""

import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from clipoteca.catalog import Catalog
from clipoteca.studio import index

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




class Writer(index.DryWriter):
    def __init__(self, root):
        super().__init__(root, Catalog([catalogue_record(1), catalogue_record(2)]))
        self.updates = []

    def update(self, incoming, *, verified_assets):
        self.updates.append((incoming, verified_assets))
        return super().update(incoming, verified_assets=verified_assets)


@pytest.fixture
def setup(tmp_path):
    work = tmp_path / "work"
    root = tmp_path / "output"
    entries = []
    for scene, numbers in (("a", (3, 4)), ("b", (4, 5))):
        directory = work / "scenes" / scene / "import"
        clips = [imported(directory, number) for number in numbers]
        put(directory / "imported.json", {"clips": clips})
        entries.extend(clips)
    put(work / "qa_report.json", {"pass": False})
    return work, root, entries, index.DryStorage(root), Writer(root)


def run(setup):
    work, _, _, storage, writer = setup
    return index.index_clips(
        work,
        repository=REPO,
        token=TOKEN,
        storage=storage,
        writer=writer,
        today=DAY,
        now=NOW,
    )


def test_all_imported_clips_indexed_without_video_or_passing_qa(setup):
    work, root, entries, _, writer = setup
    result = run(setup)
    assert set(result["clips"]) == {entry["imp_id"] for entry in entries}
    assert len(result["indexed"]) == 3
    assert len(writer.updates) == 1
    assert len(writer.updates[0][0]) == 3
    assert all(len(assets) == 3 for assets in writer.updates[0][1].values())
    assert not (work / "video.mp4").exists()
    assert not (root / "data/videos").exists()
    assert not list((root / "assets").glob("video-*"))
    assert result == index._read(work / "index.json")
    second = run(setup)
    assert second["clips"] == result["clips"]
    assert second["indexed"] == []
    assert len(writer.updates) == 1


@pytest.mark.parametrize("change", ["absolute", "traversal", "symlink", "sha", "story", "empty"])
def test_validate_all_assets_before_remote_calls(setup, change):
    work, _, _, _, writer = setup
    root = work / "scenes/a/import"
    path = root / "imported.json"
    manifest = index._read(path)
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
    with pytest.raises(index.PublishError):
        run(setup)
    assert writer.updates == []


def test_rollback(setup, monkeypatch):
    _, root, _, _, writer = setup

    def fail(*args, **kwargs):
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(writer, "update", fail)
    with pytest.raises(index.PublishError, match="remote indexing failed"):
        run(setup)
    assert not list((root / "assets").rglob("*.mp4"))
    assert not list((root / "assets").rglob("*.webp"))


def test_empty_work_needs_no_remote_clients(tmp_path):
    result = index.index_clips(tmp_path, repository="", token="")
    assert result == {"version": 1, "clips": {}, "reused_clips": [], "indexed": []}


def test_dry_run(setup, capsys):
    work, root, _, _, _ = setup
    assert index.main(["--work", str(work), "--dry-run", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["clips"]) == 3
    assert not (root / "data/videos").exists()
