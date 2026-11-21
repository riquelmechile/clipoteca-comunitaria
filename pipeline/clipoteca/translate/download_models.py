"""Download only pinned Argos models, verifying SHA-256 before installation.

Source: https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json
Pinned checksums computed from actual files downloaded on 2026-11-20.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_or_download(pins: Path, cache: Path) -> dict[str, str]:
    mapping = json.loads(pins.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    verified: dict[str, str] = {}
    for pair, spec in sorted(mapping.items()):
        if not re.fullmatch(r"(es_en|en_es|en_zh|zh_en|en_ru|ru_en)", pair):
            raise ValueError("unsupported language pair")
        name = str(spec["name"])
        version = str(spec["version"])
        checksum = str(spec["sha256"]).lower()
        url = str(spec["url"])
        filename = name + "-" + version.replace(".", "_") + ".argosmodel"
        if (
            urlsplit(url).scheme != "https"
            or urlsplit(url).hostname != "argos-net.com"
            or urlsplit(url).path != "/v1/" + filename
            or not re.fullmatch("[0-9a-f]{64}", checksum)
        ):
            raise ValueError("invalid pinned model specification")
        path = cache / filename
        if path.is_file() and digest_file(path) == checksum:
            verified[pair] = checksum
            continue
        fd, temporary = tempfile.mkstemp(prefix=".argos-", suffix=".tmp", dir=cache)
        try:
            with os.fdopen(fd, "wb") as target:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "ClipotecaCommunity/1.0"}
                )
                with urllib.request.urlopen(request, timeout=120) as response:
                    # Each package is capped, including those served chunked.
                    total = 0
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        total += len(chunk)
                        if total > 350 * 1024 * 1024:
                            raise ValueError("Argos package exceeds download limit")
                        target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if digest_file(Path(temporary)) != checksum:
                raise ValueError("downloaded Argos model SHA-256 mismatch")
            os.replace(temporary, path)
            verified[pair] = checksum
        finally:
            Path(temporary).unlink(missing_ok=True)
    if set(verified) != {"es_en", "en_es", "en_zh", "zh_en", "en_ru", "ru_en"}:
        raise ValueError("missing required Argos language pairs")
    return verified


def main() -> None:
    pins = Path(os.getenv("ARGOS_MODEL_PINS_JSON", "data/argos-pins.json"))
    cache = Path(os.getenv("ARGOS_MODEL_CACHE_DIR", ".cache/argos"))
    verified = verify_or_download(pins, cache)
    print(f"Verified {len(verified)} pinned Argos model packages")


if __name__ == "__main__":
    main()
