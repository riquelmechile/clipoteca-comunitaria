"""Verified, host-restricted Studio model downloads."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("models.json")
ALLOWED_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "huggingface.co",
        "cdn-lfs.hf.co",
        "cdn-lfs.huggingface.co",
        "cdn-lfs-eu-1.hf.co",
        "cdn-lfs-us-1.huggingface.co",
        "cdn-lfs-us-1.hf.co",
        "cas-bridge.xethub.hf.co",
    }
)


class ModelError(RuntimeError):
    pass


class ModelUnavailable(ModelError):
    pass


def cache_dir() -> Path:
    return Path(os.environ.get("CLIPOTECA_STUDIO_MODELS", ".cache/studio-models"))


def load_manifest(path: str | Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def model_spec(name: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_manifest() if manifest is None else manifest
    try:
        return data["models"][name]
    except KeyError as exc:
        raise ModelUnavailable(f"unknown model: {name}") from exc


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ModelError("model URLs must use HTTPS without credentials")
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise ModelError(f"model host is not allowed: {parsed.hostname or '<missing>'}")


class _RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _verified_asset(asset: dict[str, Any]) -> tuple[int, str]:
    size, digest = asset.get("size"), asset.get("sha256")
    if digest is None or size is None:
        raise ModelUnavailable("per_model_unpinned")
    if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
        raise ModelError("invalid model manifest metadata")
    return size, digest


def download_asset(asset: dict[str, Any], destination: str | Path) -> Path:
    """Download one exact-size, exact-digest asset atomically."""
    size, expected = _verified_asset(asset)
    urls = asset.get("urls") or []
    if not urls:
        raise ModelError("model asset has no URL")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(_RestrictedRedirect())
    last_error: Exception | None = None
    for url in urls:
        validate_url(url)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "clipoteca-studio/2.0.6"})
            digest = hashlib.sha256()
            total = 0
            with opener.open(request, timeout=60) as response, temporary_path.open("wb") as output:
                validate_url(response.geturl())
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > size:
                    raise ModelError("model download exceeds declared size")
                while chunk := response.read(min(1024 * 1024, size + 1 - total)):
                    total += len(chunk)
                    if total > size:
                        raise ModelError("model download exceeds declared size")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if total != size:
                raise ModelError(f"model size mismatch: expected {size}, got {total}")
            if digest.hexdigest() != expected:
                raise ModelError("model sha256 mismatch")
            os.replace(temporary_path, destination)
            return destination
        except (OSError, ValueError, urllib.error.URLError, ModelError) as exc:
            last_error = exc
            temporary_path.unlink(missing_ok=True)
    raise ModelError(f"all model URLs failed: {last_error}")


def safe_extract_tar(archive: str | Path, destination: str | Path) -> Path:
    """Extract a tar without links, devices, traversal, or partial destinations."""
    archive = Path(archive)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        with tarfile.open(archive, mode="r:*") as source:
            members = source.getmembers()
            for member in members:
                parts = Path(member.name).parts
                if Path(member.name).is_absolute() or ".." in parts or not parts:
                    raise ModelError("unsafe path in model archive")
                if member.issym() or member.islnk() or member.isdev():
                    raise ModelError("unsafe member type in model archive")
                if not (member.isdir() or member.isfile()):
                    raise ModelError("unsupported member type in model archive")
            for member in members:
                target = staging.joinpath(*Path(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = source.extractfile(member)
                    if stream is None:
                        raise ModelError("missing model archive member")
                    with target.open("wb") as output:
                        shutil.copyfileobj(stream, output)
        if destination.exists():
            raise ModelError("model extraction destination already exists")
        os.replace(staging, destination)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def ensure_model(name: str, root: str | Path | None = None) -> Path:
    spec = model_spec(name)
    assets = spec.get("assets", [])
    if not assets or any(asset.get("sha256") is None for asset in assets):
        raise ModelUnavailable("per_model_unpinned" if name == "w2v2-phon" else "model_unpinned")
    base = cache_dir() if root is None else Path(root)
    target = base / name
    marker = target / ".verified.json"
    if target.exists():
        expected = {asset["name"]: asset["sha256"] for asset in assets}
        try:
            if json.loads(marker.read_text(encoding="utf-8")) == expected:
                return target
        except (OSError, ValueError):
            pass
        raise ModelError("existing model cache is not verified")
    downloads = base / ".downloads"
    paths = [download_asset(asset, downloads / asset["name"]) for asset in assets]
    if spec.get("archive"):
        safe_extract_tar(paths[0], target)
        _atomic_json(marker, {asset["name"]: asset["sha256"] for asset in assets})
        return target
    target.mkdir(parents=True, exist_ok=True)
    for path in paths:
        os.replace(path, target / path.name)
    _atomic_json(marker, {asset["name"]: asset["sha256"] for asset in assets})
    return target


def per_model_status(root: str | Path | None = None) -> tuple[bool, str]:
    try:
        spec = model_spec("w2v2-phon")
    except ModelUnavailable as exc:
        return False, str(exc)
    if any(asset.get("sha256") is None for asset in spec.get("assets", [])):
        return False, "per_model_unpinned"
    base = cache_dir() if root is None else Path(root)
    return (
        (base / "w2v2-phon").exists(),
        "available" if (base / "w2v2-phon").exists() else "missing",
    )
