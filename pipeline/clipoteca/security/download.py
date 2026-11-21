"""Stream real video bytes into generated temporary files with strict caps."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import USER_AGENT
from .ssrf import UnsafeMediaURL, _is_public, validate_public_url

MAX_DOWNLOAD_BYTES = 350 * 1024 * 1024
VIDEO_TYPES = {
    "video/mp4",
    "video/x-m4v",
    "video/webm",
    "video/ogg",
    "application/ogg",
    "application/octet-stream",
}


class DownloadError(RuntimeError):
    pass


def _allowed_host(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    if not allowed_hosts:
        return True
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return any(
        host == entry.casefold().lstrip(".")
        or (entry.startswith(".") and host.endswith(entry.casefold()))
        for entry in allowed_hosts
    )


def _magic_ok(data: bytes) -> bool:
    return (
        (len(data) >= 12 and data[4:8] == b"ftyp")
        or data.startswith(bytes.fromhex("1a45dfa3"))
        or data.startswith(b"OggS")
    )


async def download_media(
    url: str,
    target_dir: Path,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    client: httpx.AsyncClient | None = None,
    resolver=None,
    max_redirects: int = 4,
    total_seconds: float | None = None,
    expected_bytes: int | None = None,
    allowed_hosts: tuple[str, ...] = (),
) -> Path:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if total_seconds is None:
        # Scale by expected size, but keep a hard wall-clock bound.
        estimated = expected_bytes if expected_bytes and expected_bytes > 0 else max_bytes
        total_seconds = min(180.0, max(45.0, 20.0 + estimated / (2 * 1024 * 1024)))
    if total_seconds <= 0:
        raise ValueError("total_seconds must be positive")
    target_dir.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(40, connect=10, read=25),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )
    fd, name = tempfile.mkstemp(prefix="clipoteca-download-", suffix=".bin", dir=target_dir)
    target = Path(name)
    os.close(fd)
    successful = False
    try:
        async with asyncio.timeout(total_seconds):
            current = url
            for hop in range(max_redirects + 1):
                if not _allowed_host(current, allowed_hosts):
                    raise DownloadError("media host outside provider allowlist")
                validated_addresses: list[str] = []
                await validate_public_url(
                    current, resolver=resolver, validated_addresses=validated_addresses
                )
                async with client.stream(
                    "GET", current, headers={"User-Agent": USER_AGENT}, follow_redirects=False
                ) as response:
                    stream = response.extensions.get("network_stream")
                    if stream is None:
                        if own_client:
                            raise DownloadError("cannot verify connected peer IP")
                    else:
                        try:
                            address = stream.get_extra_info("server_addr")
                            peer = str(address[0])
                        except (TypeError, KeyError, AttributeError, IndexError) as exc:
                            raise DownloadError("cannot inspect connected peer IP") from exc
                        if not _is_public(peer) or peer not in validated_addresses:
                            raise DownloadError("connected peer changed after DNS validation")
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or hop >= max_redirects:
                            raise DownloadError("invalid or excessive redirect chain")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise DownloadError(f"HTTP {response.status_code}")
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            declared = int(length)
                        except ValueError as exc:
                            raise DownloadError("invalid Content-Length") from exc
                        if declared > max_bytes:
                            raise DownloadError("declared media size exceeds limit")
                    kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if kind not in VIDEO_TYPES:
                        raise DownloadError("not a video MIME type")
                    total = 0
                    header = b""
                    with target.open("wb") as file:
                        async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                            total += len(chunk)
                            if total > max_bytes:
                                raise DownloadError("streamed media size exceeds limit")
                            if len(header) < 12:
                                header += chunk[: 12 - len(header)]
                            file.write(chunk)
                    if total == 0 or not _magic_ok(header):
                        raise DownloadError("response is not a recognized media container")
                    successful = True
                    return target
            raise DownloadError("exhausted redirect limit")
    except TimeoutError as exc:
        raise DownloadError("total media deadline exceeded") from exc
    except (httpx.HTTPError, UnsafeMediaURL) as exc:
        raise DownloadError(f"media fetch rejected: {type(exc).__name__}") from exc
    finally:
        if not successful:
            target.unlink(missing_ok=True)
        if own_client:
            await client.aclose()
