"""Stream real video bytes into generated temporary files with strict caps."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import USER_AGENT
from .pinning import matches_validated_ip
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
    referer: str | None = None,
    allowed_mime_types: frozenset[str] | None = None,
) -> Path:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    headers = {"User-Agent": USER_AGENT}
    if referer is not None:
        if referer != "https://www.bilibili.com/":
            raise ValueError("unexpected media Referer")
        headers["Referer"] = referer
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
        headers=headers,
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
                    "GET", current, headers=headers, follow_redirects=False
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
                        if not _is_public(peer) or not matches_validated_ip(
                            peer, validated_addresses
                        ):
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
                    if kind not in (allowed_mime_types or VIDEO_TYPES):
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


async def download_dash(
    video_url: str,
    audio_url: str,
    target_dir: Path,
    *,
    allowed_hosts: tuple[str, ...],
    referer: str,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Securely fetch separate DASH streams, then mux to MP4 with FFmpeg.

    Both transport legs have the same SSRF, peer-IP, byte and timeout checks
    as regular video downloads. FFmpeg sees local files only, never URLs.
    """
    if not allowed_hosts:
        raise DownloadError("DASH CDN allowlist required")
    video = await download_media(
        video_url,
        target_dir,
        max_bytes=max_bytes // 2,
        allowed_hosts=allowed_hosts,
        referer=referer,
    )
    audio: Path | None = None
    output = target_dir / "dash-muxed.mp4"
    try:
        audio = await download_media(
            audio_url,
            target_dir,
            max_bytes=max_bytes // 2,
            allowed_hosts=allowed_hosts,
            referer=referer,
            allowed_mime_types=frozenset(
                {
                    "audio/mp4",
                    "audio/x-m4a",
                    "application/octet-stream",
                    "video/mp4",
                }
            ),
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "mp4",
                "-protocol_whitelist",
                "file",
                "-i",
                str(video),
                "-f",
                "mp4",
                "-protocol_whitelist",
                "file",
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise DownloadError("ffmpeg_unavailable") from exc
        try:
            await asyncio.wait_for(proc.communicate(), timeout=120)
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise DownloadError("DASH mux deadline exceeded") from exc
        if proc.returncode != 0 or not output.is_file():
            raise DownloadError("DASH mux failed")
        if output.stat().st_size <= 0 or output.stat().st_size > max_bytes:
            raise DownloadError("DASH mux size invalid")
        return output
    finally:
        video.unlink(missing_ok=True)
        if audio is not None:
            audio.unlink(missing_ok=True)
