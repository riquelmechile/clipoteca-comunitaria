"""Validate and register a mobile upload using a job-scoped GitHub token."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
from PIL import Image

WORKER = "https://clipoteca-worker.clipoteca.workers.dev"
AUD = "clipoteca-media-ingest-v1"
MIB = 1024 * 1024
CHUNK = 24 * MIB
ID = re.compile(r"a\d{4}-\d{2}-[a-f0-9]{32}")
HEX = re.compile(r"[a-f0-9]{64}")
IMAGES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
VIDEOS = {"video/mp4": "mov", "video/quicktime": "mov", "video/webm": "matroska"}


class IngestError(Exception):
    """Fixed public reason code."""


def validate_manifest(raw: object, upload_id: str) -> dict:
    if not ID.fullmatch(upload_id) or not isinstance(raw, dict):
        raise IngestError("invalid_file")
    kind, mime, size = raw.get("kind"), raw.get("mime"), raw.get("bytes")
    if (
        raw.get("media_id") != upload_id
        or kind not in ("image", "video")
        or not isinstance(mime, str)
        or mime not in (IMAGES if kind == "image" else VIDEOS)
        or type(size) is not int
        or not 0 < size <= (10 if kind == "image" else 50) * MIB
    ):
        raise IngestError("invalid_file")
    for key, limit in (("title", 120), ("author", 120), ("source", 500), ("license_note", 500)):
        if key == "license_note" and key not in raw:
            continue
        value = raw.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            raise IngestError("invalid_file")
    try:
        if datetime.fromisoformat(raw["created_at"]).tzinfo is None:
            raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise IngestError("invalid_file") from exc
    chunks = raw.get("chunks")
    if not isinstance(chunks, list) or len(chunks) != (size + CHUNK - 1) // CHUNK:
        raise IngestError("invalid_file")
    for i, part in enumerate(chunks):
        if (
            not isinstance(part, dict)
            or type(part.get("bytes")) is not int
            or part["bytes"] != min(CHUNK, size - i * CHUNK)
            or not isinstance(part.get("sha256"), str)
            or not HEX.fullmatch(part["sha256"])
        ):
            raise IngestError("invalid_file")
    return raw


def assemble(manifest: dict, parts: list[Path], output: Path) -> None:
    if len(parts) != len(manifest["chunks"]):
        raise IngestError("download_failed")
    with output.open("wb") as target:
        for part, expected in zip(parts, manifest["chunks"], strict=True):
            digest = hashlib.sha256()
            size = 0
            with part.open("rb") as source:
                while block := source.read(64 * 1024):
                    size += len(block)
                    if size > expected["bytes"]:
                        raise IngestError("download_failed")
                    digest.update(block)
                    target.write(block)
            if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
                raise IngestError("download_failed")
    if output.stat().st_size != manifest["bytes"]:
        raise IngestError("download_failed")


def validate_file(manifest: dict, path: Path) -> float | None:
    mime = manifest["mime"]
    if path.stat().st_size != manifest["bytes"]:
        raise IngestError("invalid_file")
    try:
        if manifest["kind"] == "image":
            with Image.open(path, formats=[IMAGES[mime]]) as image:
                if (
                    image.format != IMAGES[mime]
                    or not 0 < image.width * image.height <= 24_000_000
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise ValueError
                image.verify()
            with Image.open(path, formats=[IMAGES[mime]]) as image:
                image.load()
            return None
        # Pin the demuxer, forbid network protocols, and keep MOV external
        # data references and absolute paths disabled (the default).
        input_args = ["-protocol_whitelist", "file,pipe", "-f", VIDEOS[mime]]
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", *input_args,
                "-show_entries",
                "format=duration:stream=codec_type,width,height,duration:stream_disposition=attached_pic",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            env={"PATH": os.environ["PATH"], "LANG": "C"},
            timeout=45,
        )
        info = json.loads(probe.stdout)
        duration = float(info["format"]["duration"])
        streams = info["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        if (
            not math.isfinite(duration)
            or not 0 < duration <= 120
            or not 0 < int(video["width"]) * int(video["height"]) <= 24_000_000
            or video.get("disposition", {}).get("attached_pic", 0)
            or len(streams) > 16
        ):
            raise ValueError
        for stream in streams:
            raw = stream.get("duration")
            if raw not in (None, "N/A"):
                seconds = float(raw)
                if not math.isfinite(seconds) or not 0 <= seconds <= 120:
                    raise ValueError
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-threads", "2",
                *input_args, "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                "-f", "null", "-",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": os.environ["PATH"], "LANG": "C"},
            timeout=180,
        )
        return duration
    except (
        OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError,
        Image.DecompressionBombError,
    ) as exc:
        raise IngestError("invalid_file") from exc


def record_for(manifest: dict, repo: str, duration: float | None) -> dict:
    upload_id = manifest["media_id"]
    tag = "uploads-" + upload_id[1:8]
    record = {
        key: manifest[key]
        for key in (
            "kind", "title", "author", "source", "license_note", "media_id",
            "bytes", "mime", "created_at",
        )
        if key in manifest
    }
    record.update(
        origin="upload",
        status="registered",
        url=f"https://github.com/{repo}/releases/download/{tag}/{upload_id}.media",
    )
    if duration is not None:
        record["duration_seconds"] = duration
    return record


class WorkerClient:
    def __init__(self, client: httpx.Client, upload_id: str, capability: str):
        self.client = client
        self.base = f"{WORKER}/v1/media/ingest/{upload_id}"
        self.capability = capability

    def headers(self) -> dict[str, str]:
        url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        url += ("&" if "?" in url else "?") + urlencode({"audience": AUD})
        response = self.client.get(
            url,
            headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
        )
        response.raise_for_status()
        return {
            "Authorization": "Bearer " + response.json()["value"],
            "X-Clipoteca-Capability": self.capability,
        }

    def claim(self) -> dict:
        response = self.client.post(self.base + "/claim", headers=self.headers())
        response.raise_for_status()
        return response.json()

    def chunk(self, index: int, expected: dict, target: Path) -> None:
        # A negative KV lookup can be cached for about 60 seconds.
        deadline = time.monotonic() + 100
        while True:
            try:
                with self.client.stream(
                    "GET", self.base + f"/chunk/{index}", headers=self.headers()
                ) as response:
                    if response.status_code in (404, 429, 500, 502, 503, 504):
                        raise IngestError("download_failed")
                    response.raise_for_status()
                    digest = hashlib.sha256()
                    size = 0
                    with target.open("wb") as out:
                        for block in response.iter_bytes(64 * 1024):
                            size += len(block)
                            if size > expected["bytes"]:
                                raise IngestError("download_failed")
                            digest.update(block)
                            out.write(block)
                    if size != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
                        raise IngestError("download_failed")
                    return
            except httpx.HTTPStatusError as exc:
                raise IngestError("download_failed") from exc
            except (httpx.RequestError, IngestError):
                target.unlink(missing_ok=True)
                if time.monotonic() >= deadline:
                    raise IngestError("download_failed") from None
                time.sleep(5)

    def finish(self, status: str, reason: str = "") -> None:
        for attempt in range(3):
            try:
                response = self.client.post(
                    self.base + "/finish",
                    headers={
                        **self.headers(),
                        "X-Clipoteca-Result": status,
                        "X-Clipoteca-Reason": reason,
                    },
                )
                response.raise_for_status()
                return
            except httpx.HTTPError:
                if attempt == 2:
                    raise
                time.sleep(3)


class GitHubPublisher:
    def __init__(self, client: httpx.Client, repo: str, token: str):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not token:
            raise IngestError("publish_failed")
        self.client = client
        self.repo = repo
        self.api = f"https://api.github.com/repos/{repo}"
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "clipoteca-media-ingest",
        }

    def release(self, tag: str) -> int:
        url = self.api + "/releases/tags/" + tag
        response = self.client.get(url, headers=self.headers)
        if response.status_code == 404:
            response = self.client.post(
                self.api + "/releases",
                headers=self.headers,
                json={
                    "tag_name": tag,
                    "name": "Aportes " + tag.removeprefix("uploads-"),
                    "target_commitish": "main",
                    "prerelease": True,
                    "draft": False,
                },
            )
            if response.status_code == 422:
                response = self.client.get(url, headers=self.headers)
        response.raise_for_status()
        data = response.json()
        if data.get("tag_name") != tag or data.get("draft") or type(data.get("id")) is not int:
            raise IngestError("publish_failed")
        return data["id"]

    def asset(self, release_id: int, name: str, mime: str, path: Path) -> None:
        size = path.stat().st_size
        url = f"https://uploads.github.com/repos/{self.repo}/releases/{release_id}/assets"
        with path.open("rb") as source:
            response = self.client.post(
                url,
                params={"name": name},
                headers={**self.headers, "Content-Type": mime, "Content-Length": str(size)},
                content=iter(lambda: source.read(64 * 1024), b""),
            )
        response.raise_for_status()
        receipt = response.json()
        if (
            response.status_code != 201
            or receipt.get("state") != "uploaded"
            or receipt.get("size") != size
            or receipt.get("name") != name
        ):
            raise IngestError("publish_failed")

    def publish(self, record: dict, media: Path) -> None:
        upload_id = record["media_id"]
        release_id = self.release("uploads-" + upload_id[1:8])
        # The JSON is the registration commit point. Never overwrite an asset.
        # A lost response can leave an orphan; do not delete a possibly committed file.
        self.asset(release_id, upload_id + ".media", record["mime"], media)
        manifest = media.with_suffix(".json")
        manifest.write_text(json.dumps(record, ensure_ascii=True), encoding="utf-8")
        self.asset(release_id, upload_id + ".json", "application/json", manifest)


def main() -> int:
    upload_id = os.environ.get("UPLOAD_ID", "")
    capability = os.environ.get("UPLOAD_CAPABILITY", "")
    if not ID.fullmatch(upload_id) or not HEX.fullmatch(capability):
        print("Identificador o capacidad no valida.")
        return 1
    with (
        httpx.Client(follow_redirects=False, timeout=httpx.Timeout(30, connect=10)) as client,
        tempfile.TemporaryDirectory(prefix="clipoteca-upload-") as directory,
    ):
        worker = WorkerClient(client, upload_id, capability)
        try:
            raw = worker.claim()
        except (httpx.HTTPError, KeyError, ValueError):
            print("No se pudo reclamar la subida.")
            return 1
        reason = "invalid_file"
        try:
            manifest = validate_manifest(raw, upload_id)
            root = Path(directory)
            parts = []
            reason = "download_failed"
            for i, expected in enumerate(manifest["chunks"]):
                part = root / f"chunk-{i}"
                worker.chunk(i, expected, part)
                parts.append(part)
            media = root / "upload.media"
            assemble(manifest, parts, media)
            reason = "invalid_file"
            duration = validate_file(manifest, media)
            reason = "publish_failed"
            repo = os.environ["GITHUB_REPOSITORY"]
            record = record_for(manifest, repo, duration)
            GitHubPublisher(client, repo, os.environ["GH_TOKEN"]).publish(record, media)
        except (IngestError, httpx.HTTPError, OSError, KeyError, ValueError):
            try:
                worker.finish("rejected", reason)
            except (httpx.HTTPError, KeyError, ValueError):
                print("No se pudo informar el rechazo; el temporal vencera.")
            print("Aporte rechazado: " + reason)
            return 1
        try:
            worker.finish("registered")
        except (httpx.HTTPError, KeyError, ValueError):
            print("Aporte publicado, pero no se pudo confirmar la limpieza temporal.")
            return 1
    print("Aporte registrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
