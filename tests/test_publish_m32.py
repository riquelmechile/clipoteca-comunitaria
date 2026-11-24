"""M32 indexing and delivery contract: synthetic bytes and local or mocked GitHub APIs."""

import hashlib
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from clipoteca.catalog import Catalog
from clipoteca.studio import delivery, index
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


class FakeGitHub:
    """Just enough of the REST API to run the REAL storage, catalogue writer."""

    def __init__(self, catalog):
        self.catalog = json.dumps(catalog, indent=2) + "\n"
        self.catalog_sha = "s0"
        self.releases = []
        self.blobs = {}
        self.calls = []
        self.serial = 100
        self.fail_once = None
        self.conflict_once = False

    def _next(self):
        self.serial += 1
        return self.serial

    def handler(self, request):
        import base64
        import re

        method, url, path = request.method, str(request.url), request.url.path
        self.calls.append((method, path))
        if self.fail_once and self.fail_once(request):
            self.fail_once = None
            return httpx.Response(500)
        if url.startswith("https://github.com/") and method == "GET":
            found = url in self.blobs
            return httpx.Response(200 if found else 404, content=self.blobs.get(url, b""))
        if path == f"/repos/{REPO}/contents/data/clips.json":
            if method == "GET":
                content = base64.b64encode(self.catalog.encode()).decode()
                body = {"sha": self.catalog_sha, "encoding": "base64", "content": content}
                return httpx.Response(200, json=body)
            body = json.loads(request.content)
            if self.conflict_once:
                self.conflict_once = False
                self.catalog = json.dumps([*json.loads(self.catalog), catalogue_record(3)])
                self.catalog_sha = "concurrent"
                return httpx.Response(409)
            if body["sha"] != self.catalog_sha:
                return httpx.Response(409)
            self.catalog = base64.b64decode(body["content"]).decode()
            self.catalog_sha = f"s{self._next()}"
            return httpx.Response(200, json={"commit": {"sha": "c1"}})
        if path == f"/repos/{REPO}/releases":
            if method == "GET":
                page = int(request.url.params.get("page", 1))
                per = int(request.url.params.get("per_page", 30))
                return httpx.Response(200, json=self.releases[(page - 1) * per : page * per])
            body = json.loads(request.content)
            if any(item["tag_name"] == body["tag_name"] for item in self.releases):
                return httpx.Response(422)
            assert body["make_latest"] == "false" and not body["draft"]
            ident = self._next()
            upload = f"https://uploads.github.com/repos/{REPO}/releases/{ident}/assets"
            release = {
                "id": ident,
                "tag_name": body["tag_name"],
                "draft": False,
                "prerelease": False,
                "assets": [],
                "upload_url": upload + "{?name,label}",
            }
            self.releases.append(release)
            return httpx.Response(201, json=release)
        listing = re.fullmatch(rf"/repos/{REPO}/releases/(\d+)/assets", path)
        if listing and method == "GET":
            release = next(x for x in self.releases if x["id"] == int(listing.group(1)))
            first = int(request.url.params.get("page", 1)) == 1
            return httpx.Response(200, json=release["assets"] if first else [])
        if request.url.host == "uploads.github.com":
            release = next(x for x in self.releases if x["id"] == int(path.split("/")[5]))
            name = request.url.params["name"]
            if any(a["name"] == name for a in release["assets"]):
                return httpx.Response(422)
            link = f"https://github.com/{REPO}/releases/download/{release['tag_name']}/{name}"
            asset = {
                "id": self._next(),
                "name": name,
                "size": len(request.content),
                "state": "uploaded",
                "browser_download_url": link,
            }
            release["assets"].append(asset)
            self.blobs[link] = request.content
            return httpx.Response(201, json=asset)
        removal = re.fullmatch(rf"/repos/{REPO}/releases/assets/(\d+)", path)
        if removal and method == "DELETE":
            for release in self.releases:
                release["assets"] = [
                    a for a in release["assets"] if a["id"] != int(removal.group(1))
                ]
            return httpx.Response(204)
        raise AssertionError((method, url))

    def index(self, work):
        from clipoteca.catalog import GitHubCatalogWriter

        client = httpx.Client(
            transport=httpx.MockTransport(self.handler),
            headers={"Authorization": "Bearer " + TOKEN},
            follow_redirects=True,
        )
        return index.index_clips(
            work,
            repository=REPO,
            token=TOKEN,
            storage=index.IndexStorage(REPO, token=TOKEN, client=client),
            writer=GitHubCatalogWriter(REPO, TOKEN, client=client),
            today=DAY,
            now=NOW,
        )

    def writes(self, since=0):
        return [call for call in self.calls[since:] if call[0] in {"POST", "PUT", "DELETE"}]


def test_real_clients_index_once_and_never_publish_video(setup):
    work, _, entries, _, _ = setup
    github = FakeGitHub([catalogue_record(1), catalogue_record(2)])
    first = github.index(work)
    assert len(first["indexed"]) == 3
    assert set(first["clips"]) == {entry["imp_id"] for entry in entries}
    assert len([call for call in github.writes() if call[0] == "PUT"]) == 1
    assert all("video-" not in release["tag_name"] for release in github.releases)
    assert all("/data/videos" not in path for _, path in github.calls)
    assert TOKEN not in github.catalog + json.dumps(first)
    seen = len(github.calls)
    again = github.index(work)
    assert again["indexed"] == [] and again["clips"] == first["clips"]
    assert github.writes(seen) == []


def test_delivery_records_failed_qa_and_all_indexed_clips(setup):
    work, root, entries, _, writer = setup
    result = run(setup)
    script = {
        "title": "El ciclo del agua",
        "aspect": "9:16",
        "language": "es",
        "music": {"mood": "chill", "bpm": 120},
        "scenes": [
            {
                "id": name,
                "type": kind,
                "title": name,
                "say": "El agua cambia.",
                "query": {"es": ["agua"], "en": ["water"]},
                "prompt": "Natural flowing water",
            }
            for name, kind in (("a", "intro"), ("b", "fact"), ("c", "outro"))
        ],
    }
    put(work / "guion.json", script)
    for name, entry in (("a", entries[0]), ("b", entries[2])):
        put(
            work / "scenes" / name / "result.json",
            {"status": "ok", "clip": {"clip_id": entry["imp_id"], "imported": True}},
        )
    put(work / "scenes/c/result.json", {"status": "ok", "clip": {"clip_id": "clip-000001"}})
    video = b"synthetic video"
    (work / "video.mp4").write_bytes(video)
    put(work / "render.json", {"duration_s": 8, "size_bytes": len(video)})
    put(
        work / "qa_report.json",
        {
            "script_id": script_id(parse_video_script(script)),
            "video": {"size_bytes": len(video), "sha256": hashlib.sha256(video).hexdigest()},
            "pass": False,
            "failed": ["audio"],
        },
    )
    put(work / "timings.json", {"render": 2})
    manifest = delivery.build_delivery(work, writer.path)
    assert manifest["qa"] == {"pass": False, "failed": ["audio"]}
    assert manifest["clips_indexed"] == result["indexed"]
    assert [row["origin"] for row in manifest["scenes"]] == ["imported", "imported", "catalog"]
    assert manifest["scenes"][0]["clip_id"] == result["clips"][entries[0]["imp_id"]]
    assert not (root / "data/videos").exists()
    (work / "video.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="mismatch"):
        delivery.build_delivery(work, writer.path)
    assert not (work / "entrega.json").exists()


def test_failed_assembly_manifest_without_video(tmp_path):
    work = tmp_path / "work"
    script = {
        "title": "El ciclo del agua",
        "aspect": "9:16",
        "scenes": [
            {
                "id": name,
                "type": "fact",
                "title": name,
                "say": "El agua cambia.",
                "query": {"es": ["agua"], "en": ["water"]},
                "prompt": "Natural flowing water",
            }
            for name in ("a", "b", "c")
        ],
    }
    put(work / "guion.json", script)
    put(work / "scenes/a/result.json", {"status": "fail", "reason": "no_candidate"})
    put(work / "scenes/a/timing.json", {"install_ffmpeg": 10, "catalog": 2})
    put(work / "scenes/a/timing-import.json", {"import": 30})
    writer = Writer(tmp_path / "catalogue")
    result = delivery.build_delivery(work, writer.path)
    assert result["sha256"] is None
    assert result["qa"]["pass"] is False
    assert result["scenes"][0]["reason"] == "no_candidate"
    assert result["timings"]["scene.a.install_ffmpeg"] == 10
    assert result["timings"]["scene.a.import"] == 30


def test_catalogue_conflict_reloads_and_reassigns_without_deleting_assets(setup, monkeypatch):
    work, _, entries, _, _ = setup
    github = FakeGitHub([catalogue_record(1), catalogue_record(2)])
    github.conflict_once = True
    monkeypatch.setattr(index.time, "sleep", lambda _: None)
    result = github.index(work)
    assert set(result["clips"]) == {entry["imp_id"] for entry in entries}
    assert "clip-000003" in result["reused_clips"]
    assert len([call for call in github.writes() if call[0] == "PUT"]) == 2
    assert not any(method == "DELETE" for method, _ in github.writes())
    published = json.loads(github.catalog)
    assert len(published) == 5
    assert len({row["id"] for row in published}) == 5
