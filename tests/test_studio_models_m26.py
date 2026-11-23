from __future__ import annotations

import hashlib
import http.client
import json
import io
import tarfile
from pathlib import Path

import pytest

from clipoteca.studio import models


class _Response(io.BytesIO):
    def __init__(self, value: bytes, url: str):
        super().__init__(value)
        self._url = url
        self.headers = {"Content-Length": str(len(value))}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _Opener:
    def __init__(self, value: bytes):
        self.value = value

    def open(self, request, timeout=0):
        return _Response(self.value, request.full_url)


def _asset(value: bytes) -> dict[str, object]:
    return {
        "name": "x.bin",
        "size": len(value),
        "sha256": hashlib.sha256(value).hexdigest(),
        "urls": ["https://github.com/example/repo/releases/download/v1/x.bin"],
    }


def test_download_verifies_sha_size_and_host(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(models.urllib.request, "build_opener", lambda *_: _Opener(b"abc"))
    assert models.download_asset(_asset(b"abc"), tmp_path / "x.bin").read_bytes() == b"abc"
    wrong = _asset(b"abc")
    wrong["sha256"] = "0" * 64
    with pytest.raises(models.ModelError, match="sha256"):
        models.download_asset(wrong, tmp_path / "bad.bin")
    large = _asset(b"ab")
    with pytest.raises(models.ModelError, match="exceeds"):
        models.download_asset(large, tmp_path / "large.bin")
    with pytest.raises(models.ModelError, match="not allowed"):
        models.validate_url("https://example.com/model.bin")


def _tar(path: Path, name: str, *, symlink: bool = False) -> None:
    with tarfile.open(path, "w") as output:
        item = tarfile.TarInfo(name)
        if symlink:
            item.type = tarfile.SYMTYPE
            item.linkname = "target"
            output.addfile(item)
        else:
            data = b"x"
            item.size = len(data)
            output.addfile(item, io.BytesIO(data))


@pytest.mark.parametrize("name,symlink", [("../x", False), ("x", True)])
def test_safe_tar_rejects_traversal_and_links(tmp_path: Path, name: str, symlink: bool) -> None:
    archive = tmp_path / "bad.tar"
    _tar(archive, name, symlink=symlink)
    with pytest.raises(models.ModelError, match="unsafe"):
        models.safe_extract_tar(archive, tmp_path / "out")


def test_null_sha_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(models.ModelUnavailable, match="per_model_unpinned"):
        models.download_asset(
            {"name": "x", "size": None, "sha256": None, "urls": ["https://github.com/x"]},
            tmp_path / "x",
        )


def test_manifest_pins_the_phoneme_model() -> None:
    assets = models.load_manifest()["models"]["w2v2-phon"]["assets"]
    assert [asset["name"] for asset in assets] == [
        "w2v2-phon-espeak.model.int8.onnx",
        "w2v2-phon-espeak.vocab.json",
    ]
    for asset in assets:
        assert isinstance(asset["size"], int) and asset["size"] > 0
        assert len(asset["sha256"]) == 64 and int(asset["sha256"], 16) >= 0


@pytest.mark.parametrize(
    "bad",
    [
        "http://github.com/x",
        "https://example.com/x",
        "https://github.com:444/x",
        "https://github.com:65536/x",
        "https://user:password@github.com/x",
    ],
)
def test_validate_url_rejects_unsafe_redirect_target(bad: str) -> None:
    with pytest.raises(models.ModelError):
        models.validate_url(bad)
    models.validate_url("https://github.com:443/x")


@pytest.mark.parametrize(
    "name,link_type",
    [
        ("/tmp/absolute.txt", None),
        ("outside", tarfile.SYMTYPE),
        ("outside", tarfile.LNKTYPE),
        ("fifo", tarfile.FIFOTYPE),
    ],
)
def test_safe_tar_rejects_special_members(
    tmp_path: Path,
    name: str,
    link_type: bytes | None,
) -> None:
    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as tar:
        item = tarfile.TarInfo(name)
        if link_type is None:
            item.size = 1
            tar.addfile(item, io.BytesIO(b"x"))
        else:
            item.type = link_type
            item.linkname = "../../outside"
            tar.addfile(item)
    with pytest.raises(models.ModelError):
        models.safe_extract_tar(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_tar_symlink_followed_by_regular_member_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as tar:
        link = tarfile.TarInfo("path")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        tar.addfile(link)
        normal = tarfile.TarInfo("path")
        normal.size = 1
        tar.addfile(normal, io.BytesIO(b"x"))
    with pytest.raises(models.ModelError):
        models.safe_extract_tar(archive, tmp_path / "target")


def test_safe_tar_member_count_limit(monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar"
    with tarfile.open(archive, "w") as tar:
        for name in ("one", "two"):
            item = tarfile.TarInfo(name)
            item.size = 1
            tar.addfile(item, io.BytesIO(b"x"))
    monkeypatch.setattr(models, "MAX_TAR_MEMBERS", 1)
    with pytest.raises(models.ModelError, match="count"):
        models.safe_extract_tar(archive, tmp_path / "target")


def test_safe_tar_expansion_limit(tmp_path: Path) -> None:
    archive = tmp_path / "compressed.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        content = b"x" * 100000
        item = tarfile.TarInfo("payload.bin")
        item.size = len(content)
        tar.addfile(item, io.BytesIO(content))
    with pytest.raises(models.ModelError, match="size"):
        models.safe_extract_tar(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_download_falls_back_from_http_exception_and_removes_temp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FallbackOpener:
        count = 0

        def open(self, request, timeout=0):
            self.count += 1
            if self.count == 1:
                raise http.client.IncompleteRead(b"x", 2)
            return _Response(b"abc", request.full_url)

    opener = FallbackOpener()
    monkeypatch.setattr(models.urllib.request, "build_opener", lambda *_: opener)
    asset = _asset(b"abc")
    asset["urls"] = [asset["urls"][0], asset["urls"][0]]
    target = tmp_path / "asset.bin"
    assert models.download_asset(asset, target).read_bytes() == b"abc"
    assert opener.count == 2
    assert list(tmp_path.iterdir()) == [target]


def test_download_cleans_temp_even_on_keyboard_interrupt(monkeypatch, tmp_path: Path) -> None:
    class InterruptOpener:
        def open(self, request, timeout=0):
            raise KeyboardInterrupt()

    monkeypatch.setattr(models.urllib.request, "build_opener", lambda *_: InterruptOpener())
    with pytest.raises(KeyboardInterrupt):
        models.download_asset(_asset(b"abc"), tmp_path / "asset.bin")
    assert not list(tmp_path.iterdir())


def test_ensure_model_repairs_unverified_cache_and_removes_download(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model = {"assets": [_asset(b"abc")], "archive": False}
    monkeypatch.setattr(models, "model_spec", lambda name: model)

    def fake_download(asset: dict[str, object], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"abc")
        return destination

    monkeypatch.setattr(models, "download_asset", fake_download)
    target = tmp_path / "sample"
    target.mkdir()
    (target / "stale.txt").write_text("old")
    (target / ".verified.json").write_text(json.dumps({"x.bin": "wrong"}))
    ready = models.ensure_model("sample", tmp_path)
    assert ready == target
    assert not (target / "stale.txt").exists()
    assert (target / "x.bin").read_bytes() == b"abc"
    assert not (tmp_path / ".downloads" / "x.bin").exists()
    assert models.ensure_model("sample", tmp_path) == target


def test_ensure_model_unpinned_manifest_does_not_download(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(models, "model_spec", lambda name: {"assets": [{"sha256": None}]})
    with pytest.raises(models.ModelUnavailable, match="per_model_unpinned"):
        models.ensure_model("w2v2-phon", tmp_path)
    assert not list(tmp_path.iterdir())
