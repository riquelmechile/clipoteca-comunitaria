import hashlib
import shutil
import subprocess

import pytest
from PIL import Image

from clipoteca.media_ingest import (
    CHUNK,
    IngestError,
    assemble,
    record_for,
    validate_file,
    validate_manifest,
)

UPLOAD_ID = "a2026-11-" + "a" * 32


def manifest(data: bytes, kind: str, mime: str) -> dict:
    return {
        "media_id": UPLOAD_ID,
        "kind": kind,
        "title": "Aporte",
        "author": "Autor",
        "source": "Celular",
        "bytes": len(data),
        "mime": mime,
        "created_at": "2026-11-25T13:00:00Z",
        "chunks": [
            {"bytes": len(part), "sha256": hashlib.sha256(part).hexdigest()}
            for i in range(0, len(data), CHUNK)
            if (part := data[i : i + CHUNK])
        ],
    }


def test_assemble_and_record(tmp_path):
    data = b"a" * CHUNK + b"b" * 13
    spec = validate_manifest(manifest(data, "video", "video/mp4"), UPLOAD_ID)
    parts = [tmp_path / "p0", tmp_path / "p1"]
    parts[0].write_bytes(data[:CHUNK])
    parts[1].write_bytes(data[CHUNK:])
    output = tmp_path / "media"
    assemble(spec, parts, output)
    assert output.read_bytes() == data
    record = record_for(spec, "riquelmechile/clipoteca-comunitaria", 3.5)
    assert record["origin"] == "upload"
    assert record["status"] == "registered"
    assert record["duration_seconds"] == 3.5
    assert record["url"].endswith(f"/uploads-2026-11/{UPLOAD_ID}.media")
    assert "chunks" not in record
    parts[1].write_bytes(b"x" * 13)
    with pytest.raises(IngestError, match="download_failed"):
        assemble(spec, parts, output)


def test_image_real_decode_and_limits(tmp_path):
    path = tmp_path / "image.jpg"
    Image.new("RGB", (20, 10)).save(path)
    spec = validate_manifest(manifest(path.read_bytes(), "image", "image/jpeg"), UPLOAD_ID)
    assert validate_file(spec, path) is None
    wrong = dict(spec, mime="image/png")
    with pytest.raises(IngestError, match="invalid_file"):
        validate_file(wrong, path)
    with pytest.raises(IngestError, match="invalid_file"):
        validate_manifest(dict(spec, bytes=11 * 1024 * 1024), UPLOAD_ID)
    path.write_bytes(b"\xff\xd8\xff\xd9")
    with pytest.raises(IngestError, match="invalid_file"):
        validate_file(manifest(path.read_bytes(), "image", "image/jpeg"), path)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg missing"
)
def test_video_real_decode(tmp_path):
    path = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=32x32:d=1",
            "-c:v",
            "mpeg4",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    spec = validate_manifest(manifest(path.read_bytes(), "video", "video/mp4"), UPLOAD_ID)
    assert 0.9 <= validate_file(spec, path) <= 1.1


def test_video_manifest_100_mib_in_five_chunks():
    size = 100 * 1024 * 1024
    spec = {
        "media_id": UPLOAD_ID,
        "kind": "video",
        "title": "Video",
        "author": "Autor",
        "source": "Aporte",
        "mime": "video/mp4",
        "bytes": size,
        "created_at": "2026-11-25T00:00:00Z",
        "chunks": [
            {"bytes": min(CHUNK, size - i), "sha256": "a" * 64}
            for i in range(0, size, CHUNK)
        ],
    }
    assert len(validate_manifest(spec, UPLOAD_ID)["chunks"]) == 5
    with pytest.raises(ValueError):
        validate_manifest(dict(spec, bytes=size + 1), UPLOAD_ID)
