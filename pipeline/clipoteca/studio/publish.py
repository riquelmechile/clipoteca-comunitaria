"""Publish a QA-approved studio video, with the record as the final commit marker."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from .. import __version__
from ..catalog import Catalog, GitHubCatalogWriter
from ..storage.github import GitHubReleaseStorage, ReleaseAsset, ReleaseBatch, StorageError
from .script import parse_video_script, script_id

_ROOT = Path(__file__).resolve().parents[3]
_VIDEO_ID = re.compile(r"video-\d{4}-\d{2}-\d{2}-\d{3,}\Z")
_CLIP_ID = re.compile(r"clip-\d{6}\Z")
_IMP_ID = re.compile(r"imp-[a-f0-9]{12}\Z")


class PublishError(ValueError):
    """Invalid or rejected publication input, without upstream response details."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _bytes(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _digest(path):
    hashed = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hashed.update(block)
            size += len(block)
    return size, hashed.hexdigest()


def _validator(name):
    return Draft202012Validator(
        _read(_ROOT / "schemas" / name), format_checker=FormatChecker()
    )


def _local(root, relative):
    if not isinstance(relative, str) or not relative or any(c in relative for c in "\\:\0"):
        raise PublishError("unsafe imported asset path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PublishError("unsafe imported asset path")
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise PublishError("unsafe imported asset root")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise PublishError("unsafe imported asset path")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise PublishError("missing or unsafe imported asset")
    return current


def _asset(root, relative, *, preview=False):
    path = _local(root, relative)
    if preview and not 0 < path.stat().st_size <= 5 * 1024 * 1024:
        raise PublishError("invalid preview size")
    size, digest = _digest(path)
    if size <= 0 or (preview and size > 5 * 1024 * 1024):
        raise PublishError("invalid asset size")
    return path, size, digest


def _imported(root, identifier, clip):
    if not _IMP_ID.fullmatch(identifier):
        raise PublishError("invalid imported identifier")
    entries = _read(_local(root, "imported.json"))["clips"]
    matching = [entry for entry in entries if entry["imp_id"] == identifier]
    if len(matching) != 1:
        raise PublishError("missing or ambiguous imported clip")
    entry = matching[0]
    record = deepcopy(entry["record"])
    assets = entry["assets"]
    video = _asset(root, assets["video"])
    if (
        video[1:] != (entry["size_bytes"], entry["sha256"])
        or video[1:] != (record["media"]["size_bytes"], record["media"]["sha256"])
        or identifier != "imp-" + video[2][:12]
        or clip["local_path"] != assets["video"]
        or clip["source_url"] != record["source"]["source_url"]
    ):
        raise PublishError("imported video integrity mismatch")
    stories = assets["storyboard"]
    urls = record["storage"].get("storyboard_urls")
    if (
        not isinstance(stories, list)
        or len(stories) > 8
        or len(stories) != len(set(stories))
        or (urls is not None and len(stories) != len(urls))
    ):
        raise PublishError("inconsistent storyboard")
    previews = [_asset(root, assets["thumbnail"], preview=True)]
    previews.extend(_asset(root, path, preview=True) for path in stories)
    if len({item[0] for item in [video, *previews]}) != 2 + len(stories):
        raise PublishError("repeated imported asset")
    return record, [video, *previews]


def _record_clip(record, identifier, created, urls):
    value = deepcopy(record)
    value["id"] = identifier
    value["created_at"] = created
    value["storage"] = {
        "provider": "github_releases",
        "asset_url": urls[0],
        "thumbnail_url": urls[1],
    }
    if len(urls) > 2:
        value["storage"]["storyboard_urls"] = urls[2:]
    encoded = json.dumps(value)
    if "local://" in encoded or re.search(r"imp-[a-f0-9]{12}", encoded):
        raise PublishError("provisional clip metadata")
    _validator("clip.schema.json").validate(value)
    return value


def _gate(work):
    try:
        qa = _read(work / "qa_report.json")
        raw = _read(work / "guion.json")
        script = parse_video_script(raw)
        video = work / "video.mp4"
        size, digest = _digest(video)
        render = _read(work / "render.json")
        if (
            qa["pass"] is not True
            or qa["failed"] != []
            or qa["video"]["sha256"] != digest
            or qa["video"]["size_bytes"] != size
            or qa["script_id"] != script_id(script)
            or render["size_bytes"] != size
            or not size
        ):
            raise PublishError("QA publication gate rejected", 4)
        rows = []
        for scene in script.scenes:
            result = _read(work / "scenes" / scene.id / "result.json")
            if result["status"] != "ok":
                raise PublishError("scene publication gate rejected", 4)
            rows.append((scene.id, result["clip"]))
    except PublishError:
        raise
    except Exception:
        raise PublishError("missing or invalid publication gate input", 4) from None
    return raw, script, qa, video, size, digest, render, rows


def _prepare(work, created):
    raw, script, qa, video, size, digest, render, rows = _gate(work)
    _read(work / "timeline.json")
    imports = {}
    scenes = []
    for scene_id, clip in rows:
        identifier = clip["clip_id"]
        imported = clip.get("imported", False)
        if not isinstance(imported, bool):
            raise PublishError("invalid imported flag")
        if imported:
            record, assets = _imported(work / "scenes" / scene_id / "import", identifier, clip)
            urls = [f"https://example.invalid/{index}" for index in range(len(assets))]
            _record_clip(record, "clip-999999", created, urls)
            if identifier in imports and imports[identifier][1][0][1:] != assets[0][1:]:
                raise PublishError("ambiguous imported identity")
            imports.setdefault(identifier, (record, assets))
        elif not _CLIP_ID.fullmatch(identifier):
            raise PublishError("invalid catalogue clip identifier")
        scenes.append(
            {
                "id": scene_id,
                "clip_id": identifier,
                "imported": imported,
                "source_url": clip["source_url"],
                "start": clip["start"],
                "len": clip["len"],
            }
        )
    checks = qa["checks"]
    if not checks or any(check["pass"] is not True for check in checks.values()):
        raise PublishError("QA checks rejected", 4)
    timings_path = work / "timings.json"
    timings = _read(timings_path) if timings_path.exists() else {}
    timings.update(render_s=render["render_s"], mux_s=render["mux_s"])
    record = {
        "version": 1,
        "created_at": created,
        "script_id": qa["script_id"],
        "title": script.title,
        "aspect": script.aspect,
        "language": script.language,
        "media": {
            **{key: render[key] for key in ("duration_s", "width", "height", "fps")},
            "size_bytes": size,
            "sha256": digest,
        },
        "script": raw,
        "scenes": scenes,
        "qa": {
            "pass": True,
            "checks": dict.fromkeys(checks, True),
            "wer": checks["whisper_wer"]["wer"],
            "sync_mean_ms": checks["subtitle_sync"]["mean_ms"],
            "sync_p95_ms": checks["subtitle_sync"]["p95_ms"],
            "lufs": checks["loudness_ebur128"]["lufs"],
            "true_peak_dbfs": checks["loudness_ebur128"]["true_peak_dbfs"],
        },
        "timings": timings,
        "engine": {
            "clipoteca": __version__,
            "hyperframes": render["hyperframes"],
            "gsap_sha256": render["gsap_sha256"],
        },
    }
    # Validate the entire prospective record before any remote calls.
    sample = deepcopy(record)
    for scene in sample["scenes"]:
        if scene["imported"]:
            scene["clip_id"] = "clip-999999"
    sample["id"] = "video-2000-01-01-001"
    sample["storage"] = {
        "release_tag": sample["id"],
        "asset_url": "https://example.invalid/video.mp4",
    }
    _validator("video-record.schema.json").validate(sample)
    return record, imports, (video, size, digest)


def _upload(storage, release, asset, name, uploaded=None):
    path, size, digest = asset
    if _digest(path) != (size, digest):
        raise PublishError("asset changed before upload")
    existing = storage.find_existing_asset(release, name, size, digest)
    if existing is None:
        existing = storage.upload_asset(release, path, name)
        if uploaded is not None:
            uploaded.append(existing.id)
    return storage.verify_asset(name, size, digest, release.id)


def _publish_clips(storage, writer, imports, scenes, day, created):
    catalog, _ = writer.read_catalog()
    known = {clip["id"] for clip in catalog.clips}
    if any(not row["imported"] and row["clip_id"] not in known for row in scenes):
        raise PublishError("catalogue scene clip missing")
    working = Catalog(catalog.clips)
    mapping, reused, incoming, verified, uploaded = {}, [], [], {}, []
    release = None
    reserved = set()
    try:
        for imp_id, (record, assets) in imports.items():
            match = next(
                (
                    clip
                    for clip in working.clips
                    if clip["media"]["sha256"] == record["media"]["sha256"]
                ),
                None,
            ) or working.find_duplicate(record)
            if match is not None:
                mapping[imp_id] = match["id"]
                reused.append(match["id"])
                continue
            if release is None:
                release = storage.get_or_create_batch_release(day)
                reserved = set(storage.reserved_ids(release))
            identifier = working.next_id(reserved_ids=reserved)
            names = [f"clip_{identifier}.mp4", f"thumb_{identifier}.webp"]
            names.extend(f"story_{identifier}_{i:02d}.webp" for i in range(len(assets) - 2))
            pair = tuple(
                _upload(storage, release, asset, name, uploaded)
                for asset, name in zip(assets, names, strict=True)
            )
            clip = _record_clip(record, identifier, created, [a.browser_download_url for a in pair])
            working.merge([clip])
            incoming.append(clip)
            verified[identifier] = pair
            mapping[imp_id] = identifier
        if incoming:
            writer.update(incoming, verified_assets=verified)
    except Exception:
        for asset_id in reversed(uploaded):
            try:
                storage.delete_asset(asset_id)
            except Exception:
                # Do not mask the publication failure or leak an upstream response.
                pass
        raise
    return mapping, sorted(set(reused))


class VideoStorage(GitHubReleaseStorage):
    """Reuse the immutable asset API for standalone video releases."""

    def video_release(self, day, size, digest):
        prefix = f"video-{day.isoformat()}-"
        minimum = 1
        for attempt in range(4):
            releases = []
            for page in range(1, 101):
                batch = self._request(
                    "GET", self.releases_url, params={"per_page": 100, "page": page}
                ).json()
                releases.extend(batch)
                if len(batch) < 100:
                    break
            number = minimum
            for item in releases:
                tag = item.get("tag_name", "")
                if not _VIDEO_ID.fullmatch(tag):
                    continue
                if tag.startswith(prefix):
                    number = max(number, int(tag.rsplit("-", 1)[1]) + 1)
                if item.get("draft") or item.get("prerelease"):
                    continue
                release = self._batch(item)
                name = f"video_{tag}.mp4"
                if not any(
                    a.get("name") == name and a.get("size") == size
                    for a in self._list_assets(release.id)
                ):
                    continue
                try:
                    self.verify_asset(name, size, digest, release.id)
                except StorageError:
                    continue
                return release
            tag = f"{prefix}{number:03d}"
            response = self.client.post(
                self.releases_url,
                json={
                    "tag_name": tag,
                    "name": tag,
                    "draft": False,
                    "prerelease": False,
                    "make_latest": "false",
                },
            )
            if response.status_code == 422 and attempt < 3:
                minimum = number + 1
                continue
            response.raise_for_status()
            return self._batch(response.json())
        raise StorageError("video release conflicts")


class VideoRecords:
    """Immutable publication marker written using optimistic Contents API."""

    def __init__(self, repository, token, *, client=None):
        self.client = client or httpx.Client(
            headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=30,
        )
        self.url = f"https://api.github.com/repos/{repository}/contents/data/videos"

    def read(self, identifier):
        if not _VIDEO_ID.fullmatch(identifier):
            raise PublishError("invalid video identifier")
        response = self.client.get(f"{self.url}/{identifier}.json")
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        payload = response.json()
        if payload["encoding"] != "base64":
            raise StorageError("invalid video record encoding")
        record = json.loads(base64.b64decode(payload["content"]))
        _validator("video-record.schema.json").validate(record)
        return record, payload["sha"]

    def find(self, digest):
        response = self.client.get(self.url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        for item in response.json():
            name = item["name"]
            if item.get("type") != "file" or not name.endswith(".json"):
                continue
            identifier = name[:-5]
            if not _VIDEO_ID.fullmatch(identifier):
                continue
            record, _ = self.read(identifier)
            if record and record["media"]["sha256"] == digest:
                return record
        return None

    def save(self, record):
        identifier = record["id"]
        for attempt in range(4):
            existing, sha = self.read(identifier)
            if existing is not None:
                if existing["media"]["sha256"] == record["media"]["sha256"]:
                    return existing
                raise StorageError("video record identity conflict")
            payload = {
                "message": f"chore(videos): publish {identifier}",
                "content": base64.b64encode(_bytes(record)).decode(),
            }
            if sha:
                payload["sha"] = sha
            response = self.client.put(f"{self.url}/{identifier}.json", json=payload)
            if response.status_code in (409, 422) and attempt < 3:
                continue
            response.raise_for_status()
            return record
        raise StorageError("video record conflicts")


def _finish(work, record, mapping, reused, already):
    identifier = record["id"]
    result = {
        "version": 1,
        "video_id": identifier,
        "asset_url": record["storage"]["asset_url"],
        "record_path": f"data/videos/{identifier}.json",
        "clips": mapping,
        "reused_clips": reused,
        "already_published": already,
    }
    _atomic(work / "publish.json", result)
    return result


def publish_video(
    work_dir, *, repository, token, storage=None, writer=None, records=None, today=None, now=None
):
    """Perform the gate, catalogue transaction, release and final record in that order."""
    work = Path(work_dir)
    moment = now or datetime.now(UTC)
    created = moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    day = today or moment.astimezone(UTC).date()
    try:
        record, imports, asset = _prepare(work, created)
        if token and token in json.dumps(record):
            raise PublishError("credential in publication input")
        if token and any(token in json.dumps(item[0]) for item in imports.values()):
            raise PublishError("credential in publication input")
    except PublishError:
        raise
    except Exception:
        raise PublishError("invalid publication input") from None
    owned = []
    try:
        if storage is None:
            storage = VideoStorage(repository, token=token)
            owned.append(storage.client)
        if writer is None:
            writer = GitHubCatalogWriter(repository, token)
            owned.append(writer.client)
        if records is None:
            records = VideoRecords(repository, token)
            owned.append(records.client)
        mapping, reused = _publish_clips(storage, writer, imports, record["scenes"], day, created)
        for scene in record["scenes"]:
            if scene["imported"]:
                scene["clip_id"] = mapping[scene["clip_id"]]
        previous = records.find(asset[2])
        if previous is not None:
            return _finish(work, previous, mapping, reused, True)
        release = storage.video_release(day, asset[1], asset[2])
        name = f"video_{release.tag}.mp4"
        verified = _upload(storage, release, asset, name)
        record["id"] = release.tag
        record["storage"] = {"release_tag": release.tag, "asset_url": verified.browser_download_url}
        _validator("video-record.schema.json").validate(record)
        saved = records.save(record)
        return _finish(work, saved, mapping, reused, False)
    except PublishError:
        raise
    except Exception:
        raise PublishError("remote publication failed", 1) from None
    finally:
        for client in owned:
            client.close()


class DryWriter:
    def __init__(self, root, catalog):
        self.path = root / "data" / "clips.json"
        self.catalog = Catalog.load(self.path) if self.path.exists() else catalog
        self.catalog.save(self.path)

    def read_catalog(self):
        return self.catalog, "dry"

    def update(self, incoming, *, verified_assets):
        self.catalog.merge(incoming)
        self.catalog.save(self.path)
        return "dry"


class DryRecords:
    def __init__(self, root):
        self.root = root / "data" / "videos"

    def find(self, digest):
        for path in sorted(self.root.glob("video-*.json")):
            record = _read(path)
            if record["media"]["sha256"] == digest:
                return record
        return None

    def save(self, record):
        _atomic(self.root / f"{record['id']}.json", record)
        return record


class DryStorage:
    """Filesystem-only implementation of the release storage contract."""

    def __init__(self, root):
        self.root = root / "assets"
        self._ids = {}

    def _release(self, tag):
        number = int(hashlib.sha256(tag.encode()).hexdigest()[:12], 16)
        self._ids[number] = tag
        return ReleaseBatch(number, tag, "")

    def get_or_create_batch_release(self, day):
        return self._release(f"batch-{day.isoformat()}-001")

    def reserved_ids(self, release):
        folder = self.root / release.tag
        return {path.stem.removeprefix("clip_") for path in folder.glob("clip_*.mp4")}

    def video_release(self, day, size, digest):
        prefix = f"video-{day.isoformat()}-"
        number = 1
        for folder in sorted(self.root.glob("video-*")):
            tag = folder.name
            if not _VIDEO_ID.fullmatch(tag):
                continue
            if tag.startswith(prefix):
                number = max(number, int(tag.rsplit("-", 1)[1]) + 1)
            path = folder / f"video_{tag}.mp4"
            if path.is_file() and _digest(path) == (size, digest):
                return self._release(tag)
        return self._release(f"{prefix}{number:03d}")

    def find_existing_asset(self, release, name, size, digest):
        path = self.root / release.tag / name
        if not path.exists():
            return None
        return self.verify_asset(name, size, digest, release.id)

    def upload_asset(self, release, path, name):
        dest = self.root / release.tag / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        return self.verify_asset(name, *_digest(path), release.id)

    def verify_asset(self, name, size, digest, release_id):
        tag = self._ids[release_id]
        path = self.root / tag / name
        if _digest(path) != (size, digest):
            raise StorageError("dry asset mismatch")
        identifier = int(hashlib.sha256(f"{tag}/{name}".encode()).hexdigest()[:12], 16)
        self._ids[identifier] = path
        url = f"https://github.com/example/clipoteca/releases/download/{tag}/{name}"
        return ReleaseAsset(identifier, name, size, "uploaded", url)

    def delete_asset(self, asset_id):
        self._ids[asset_id].unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.publish")
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--dry-run", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.dry_run is not None:
            # The gate runs before constructing even the local doubles.
            _gate(args.work)
            root = args.dry_run
            catalog = Catalog.load(_ROOT / "data" / "clips.json")
            result = publish_video(
                args.work,
                repository="",
                token="",
                storage=DryStorage(root),
                writer=DryWriter(root, catalog),
                records=DryRecords(root),
            )
        else:
            repository = os.environ.get("GITHUB_REPOSITORY")
            token = os.environ.get("GITHUB_TOKEN")
            if not repository or not token:
                raise PublishError("missing publication credentials")
            result = publish_video(args.work, repository=repository, token=token)
    except PublishError as exc:
        print(json.dumps({"error": str(exc)}))
        return exc.code
    except Exception:
        print(json.dumps({"error": "invalid publication input"}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
