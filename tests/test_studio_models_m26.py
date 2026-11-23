from __future__ import annotations

import hashlib
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
