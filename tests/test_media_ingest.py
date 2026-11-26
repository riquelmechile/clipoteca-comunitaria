import base64
import hashlib
import json
import shutil
import subprocess

import httpx
import pytest
from PIL import Image

from clipoteca.media_ingest import (
    CHUNK,
    IngestError,
    enrich_metadata,
    GitHubPublisher,
    assemble,
    record_for,
    normalize_upload,
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
    Image.new("RGB", (400, 360)).save(path)
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
            "color=c=black:s=360x360:d=1",
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
            {"bytes": min(CHUNK, size - i), "sha256": "a" * 64} for i in range(0, size, CHUNK)
        ],
    }
    assert len(validate_manifest(spec, UPLOAD_ID)["chunks"]) == 5
    with pytest.raises(IngestError):
        validate_manifest(dict(spec, bytes=size + 1), UPLOAD_ID)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg missing"
)
@pytest.mark.parametrize(
    "size,expected",
    [
        ("1920x1080", (1280, 720)),
        ("1080x1920", (720, 1280)),
        ("464x688", (464, 688)),
        ("360x360", (360, 360)),
    ],
)
def test_normalize_video_preserves_frame(tmp_path, size, expected):
    path = tmp_path / "original.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={size}:d=1",
            "-c:v",
            "mpeg4",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    spec = manifest(path.read_bytes(), "video", "video/mp4")
    normalized, target, duration = normalize_upload(spec, path, tmp_path)
    assert (normalized["width"], normalized["height"]) == expected
    assert normalized["mime"] == "video/mp4"
    assert normalized["low_resolution"] == (min(expected) < 720)
    record = record_for(normalized, "riquelmechile/clipoteca-comunitaria", duration)
    assert record["low_resolution"] == (min(expected) < 720)
    assert normalized["codec"] == "h264"
    assert normalized["bytes"] == target.stat().st_size
    assert 0.9 <= duration <= 1.1
    assert target != path


def test_normalize_image(tmp_path):
    path = tmp_path / "original.png"
    Image.new("RGBA", (2400, 1200), (255, 0, 0, 100)).save(path)
    spec = manifest(path.read_bytes(), "image", "image/png")
    normalized, target, duration = normalize_upload(spec, path, tmp_path)
    assert normalized["mime"] == "image/webp"
    assert duration is None
    with Image.open(target) as image:
        assert image.size == (1920, 960)
        assert image.mode == "RGBA"


def test_descriptive_metadata():
    spec = manifest(b"media", "video", "video/mp4")
    spec["description"] = {"es": "Lluvia"}
    assert validate_manifest(spec, UPLOAD_ID)["description"] == {"es": "Lluvia"}
    for wrong in ({"en": "Rain"}, {"es": ""}, {"es": "Lluvia", "fr": "Pluie"}):
        with pytest.raises(IngestError):
            validate_manifest(dict(spec, description=wrong), UPLOAD_ID)
    for wrong in ({"es": []}, {"en": [""]}):
        with pytest.raises(IngestError):
            validate_manifest(dict(spec, tags=wrong), UPLOAD_ID)


def test_enrich_metadata_uses_existing_translation(monkeypatch):
    translations = {"Lluvia en el campo": "Rain in the field", "lluvia": "rain", "campo": "field"}

    def translator(value, src, dst):
        assert (src, dst) == ("es", "en")
        return translations[value]

    monkeypatch.setattr("clipoteca.media_ingest.translate_text", translator)
    original = {"description": {"es": "Lluvia en el campo"}}
    result = enrich_metadata(original)
    assert result["description"]["en"] == "Rain in the field"
    assert result["tags"] == {"es": ["lluvia", "campo"], "en": ["rain", "field"]}
    assert result["metadata_auto"] == ["description.en", "tags.es", "tags.en"]
    assert original == {"description": {"es": "Lluvia en el campo"}}


def test_enrich_metadata_failure_preserves_spanish(monkeypatch, capsys):
    def translator(*args):
        raise RuntimeError("offline")

    monkeypatch.setattr("clipoteca.media_ingest.translate_text", translator)
    spec = manifest(b"media", "video", "video/mp4")
    spec["description"] = {"es": "Lluvia en el campo"}
    result = record_for(enrich_metadata(validate_manifest(spec, UPLOAD_ID)), "owner/repo", 1)
    assert result["status"] == "registered"
    assert result["description"] == spec["description"]
    assert result["tags"] == {"es": ["lluvia", "campo"]}
    assert result["metadata_auto"] == ["tags.es"]
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "offline" not in output


def test_index_optimistic_retry_and_idempotency(monkeypatch):
    monkeypatch.setattr("clipoteca.media_ingest.time.sleep", lambda _: None)
    rows = []
    writes = []
    record = {
        "media_id": UPLOAD_ID,
        "description": {"es": "Lluvia"},
        "tags": {"es": ["lluvia"]},
        "status": "registered",
    }

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "sha": "sha",
                    "encoding": "base64",
                    "content": base64.b64encode(json.dumps(rows).encode()).decode(),
                },
            )
        payload = json.loads(request.content)
        writes.append(payload)
        if len(writes) == 1:
            return httpx.Response(409)
        rows[:] = json.loads(base64.b64decode(payload["content"]))
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = GitHubPublisher(client, "owner/repo", "token")
        publisher.index(record)
        publisher.index(record)
    assert rows == [record]
    assert len(writes) == 2


def test_enrich_metadata_unchanged_tag_and_existing_provenance(monkeypatch, capsys):
    from clipoteca.translate.argos import TranslationFailed

    def translator(value, src, dst):
        if value == "vertical":
            raise TranslationFailed(src, dst, "unchanged_translation")
        return {"Lluvia vertical": "Vertical rain", "lluvia": "rain"}[value]

    monkeypatch.setattr("clipoteca.media_ingest.translate_text", translator)
    original = {
        "description": {"es": "Lluvia vertical"},
        "tags": {"es": ["lluvia", "vertical"]},
        "metadata_auto": ["tags.es"],
    }
    result = enrich_metadata(original)
    assert result["description"]["en"] == "Vertical rain"
    assert result["tags"] == {"es": ["lluvia", "vertical"], "en": ["rain"]}
    assert result["metadata_auto"] == ["tags.es", "description.en", "tags.en"]
    assert "unchanged_translation" in capsys.readouterr().out
    assert enrich_metadata(result) == result


def test_reject_small_image_before_publish(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (464, 359)).save(path)
    with pytest.raises(IngestError, match="resolution_too_low"):
        normalize_upload(manifest(path.read_bytes(), "image", "image/png"), path, tmp_path)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg missing"
)
def test_reject_small_video_before_publish(tmp_path):
    path = tmp_path / "small.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=320x180:d=1",
            "-c:v",
            "mpeg4",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    with pytest.raises(IngestError, match="resolution_too_low"):
        normalize_upload(manifest(path.read_bytes(), "video", "video/mp4"), path, tmp_path)
