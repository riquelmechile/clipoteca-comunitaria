"""Index all quality-filtered imported clips, independently of video assembly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from ..catalog import Catalog, DuplicateClip, GitHubCatalogWriter, IdentityCollision
from ..storage.github import GitHubReleaseStorage, ReleaseAsset, ReleaseBatch, StorageError

_ROOT = Path(__file__).resolve().parents[3]
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
    return Draft202012Validator(_read(_ROOT / "schemas" / name), format_checker=FormatChecker())


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
    encoded = json.dumps({key: item for key, item in value.items() if key not in {"id", "storage"}})
    old_id = record["id"]
    if (
        "local://" in json.dumps(value)
        or re.search(r"imp-[a-f0-9]{12}", json.dumps(value))
        or old_id in encoded
    ):
        raise PublishError("provisional clip metadata")
    _validator("clip.schema.json").validate(value)
    return value


def _public_asset_url(value, repository, token):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or (repository and not parsed.path.startswith(f"/{repository}/releases/download/"))
        or "/releases/download/" not in parsed.path
        or (token and token in value)
    ):
        raise PublishError("unsafe verified asset URL", 1)
    return value


class ConcurrentWrite(StorageError):
    """A reserved asset name or optimistic catalogue write was contested."""


class IndexStorage(GitHubReleaseStorage):
    def upload_asset(self, release, path, name):
        # Unlike the ordinary serial writer, never adopt an in-flight named asset.
        response = self.client.post(
            release.upload_url,
            params={"name": name},
            headers={"Content-Type": "application/octet-stream"},
            content=path.read_bytes(),
        )
        if response.status_code in (409, 422):
            raise ConcurrentWrite("asset reservation conflict")
        response.raise_for_status()
        return self._asset(response.json())


def _upload(storage, release, asset, name, uploaded=None):
    path, size, digest = asset
    if _digest(path) != (size, digest):
        raise PublishError("asset changed before upload")
    existing = storage.find_existing_asset(release, name, size, digest)
    if existing is not None:
        raise ConcurrentWrite("asset already reserved")
    existing = storage.upload_asset(release, path, name)
    if uploaded is not None:
        uploaded.append(existing.id)
    return storage.verify_asset(name, size, digest, release.id)


def _publish_clips(storage, writer, imports, day, created, repository, token):
    catalog, _ = writer.read_catalog()
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
            urls = [_public_asset_url(a.browser_download_url, repository, token) for a in pair]
            clip = _record_clip(record, identifier, created, urls)
            working.merge([clip])
            incoming.append(clip)
            verified[identifier] = pair
            mapping[imp_id] = identifier
        if incoming:
            if isinstance(writer, GitHubCatalogWriter):
                writer.update(incoming, verified_assets=verified, retries=0)
            else:
                writer.update(incoming, verified_assets=verified)
    except Exception as exc:
        # Legacy ingest may adopt identical named bytes while we are uploading.
        # In real concurrent storage, never delete reservations on errors, including
        # ambiguous PUT outcomes. The next attempt re-reads and picks a fresh ID.
        if isinstance(storage, IndexStorage) or _retryable(exc):
            raise
        # A lost PUT response can mean the optimistic commit actually succeeded.
        # Never delete assets referenced by an observed committed catalogue.
        committed = set()
        if incoming:
            try:
                latest, _ = writer.read_catalog()
                published = {clip["id"]: clip for clip in latest.clips}
                for clip in incoming:
                    if published.get(clip["id"]) == clip:
                        committed.update(asset.id for asset in verified[clip["id"]])
            except Exception:
                # If commit state cannot be determined, leave assets reserved.
                raise StorageError("catalogue commit outcome unknown") from None
        for asset_id in reversed(uploaded):
            if asset_id in committed:
                continue
            try:
                storage.delete_asset(asset_id)
            except Exception:
                # Do not mask the publication failure or leak an upstream response.
                pass
        raise
    return mapping, sorted(set(reused)), [clip["id"] for clip in incoming]


def _retryable(exc):
    if isinstance(exc, (ConcurrentWrite, DuplicateClip, IdentityCollision)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (409, 422)
    if isinstance(exc, StorageError):
        return "asset name collision:" in str(exc)
    return isinstance(exc, RuntimeError) and "catalogue conflict" in str(exc)


def _publish_with_retry(storage, writer, imports, day, created, repository, token):
    for attempt in range(6):
        try:
            return _publish_clips(storage, writer, imports, day, created, repository, token)
        except Exception as exc:
            if not _retryable(exc) or attempt == 5:
                raise
            time.sleep(min(8, 0.5 * 2**attempt))
    raise AssertionError("unreachable")


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


def _collect(work, created):
    imports = {}
    paths = [
        path
        for scene in sorted((work / "scenes").glob("*"))
        for name in ("import", "import-retry")
        if (path := scene / name / "imported.json").is_file()
    ]
    for path in paths:
        root = path.parent
        manifest = _read(_local(root, "imported.json"))
        for entry in manifest["clips"]:
            identifier = entry["imp_id"]
            record, assets = _imported(root, identifier, entry)
            urls = [f"https://example.invalid/{i}" for i in range(len(assets))]
            _record_clip(record, "clip-999999", created, urls)
            if identifier in imports and imports[identifier][1][0][1:] != assets[0][1:]:
                raise PublishError("ambiguous imported identity")
            imports.setdefault(identifier, (record, assets))
    return imports


def index_clips(work_dir, *, repository, token, storage=None, writer=None, today=None, now=None):
    """Verify every imported asset before a single catalogue update, without touching video."""
    work = Path(work_dir)
    (work / "index.json").unlink(missing_ok=True)
    moment = now or datetime.now(UTC)
    created = moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
    day = today or moment.astimezone(UTC).date()
    try:
        imports = _collect(work, created)
        if token and any(token in json.dumps(item[0]) for item in imports.values()):
            raise PublishError("credential in index input")
    except PublishError:
        raise
    except Exception:
        raise PublishError("invalid index input") from None
    owned = []
    try:
        mapping, reused, indexed = {}, [], []
        if imports:
            if storage is None:
                storage = IndexStorage(repository, token=token)
                owned.append(storage.client)
            if writer is None:
                writer = GitHubCatalogWriter(repository, token)
                owned.append(writer.client)
            mapping, reused, indexed = _publish_with_retry(
                storage, writer, imports, day, created, repository, token
            )
        result = {"version": 1, "clips": mapping, "reused_clips": reused, "indexed": indexed}
        _atomic(work / "index.json", result)
        return result
    except Exception:
        raise PublishError("remote indexing failed", 1) from None
    finally:
        for client in owned:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.index")
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--dry-run", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.dry_run is not None:
            result = index_clips(
                args.work,
                repository="",
                token="",
                storage=DryStorage(args.dry_run),
                writer=DryWriter(args.dry_run, Catalog.load(_ROOT / "data" / "clips.json")),
            )
        else:
            repository = os.environ.get("GITHUB_REPOSITORY")
            token = os.environ.get("GITHUB_TOKEN")
            if not repository or not token:
                raise PublishError("missing indexing credentials")
            result = index_clips(args.work, repository=repository, token=token)
    except PublishError as exc:
        print(json.dumps({"error": str(exc)}))
        return exc.code
    except Exception:
        print(json.dumps({"error": "invalid index input"}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
