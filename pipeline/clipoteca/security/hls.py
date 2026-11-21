"""Bounded, SSRF-checked HLS staging before local FFmpeg remux.

Unlike passing arbitrary remote playlists to FFmpeg, every URI is validated,
each HTTP connection checks its peer, and the local playlist has file paths
only. This avoids playlist mutation and DNS rebinding at the FFmpeg boundary.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import USER_AGENT
from ..media.process_env import safe_media_env
from .download import DownloadError, MAX_DOWNLOAD_BYTES
from .ssrf import UnsafeMediaURL, _is_public, validate_public_url

HLS_MIME_TYPES = frozenset({
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
})
MAX_PLAYLIST_BYTES = 256 * 1024
MAX_SEGMENTS = 80
MAX_HLS_DURATION = 20.0
STREAM_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")
BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)")


def _lines(playlist: str) -> list[str]:
    if not playlist.lstrip().startswith("#EXTM3U"):
        raise DownloadError("invalid HLS playlist")
    rows = [row.strip() for row in playlist.splitlines() if row.strip()]
    if len(rows) > 1200:
        raise DownloadError("HLS playlist too large")
    if any(row.startswith("#EXT-X-KEY") or row.startswith("#EXT-X-MAP") for row in rows):
        # No AES keys or byte-range fMP4 segments are fetched implicitly.
        raise DownloadError("HLS key or init-map requires separate support")
    if any(row.startswith("#EXT-X-BYTERANGE") for row in rows):
        raise DownloadError("HLS byte-range segments are unsupported")
    return rows


def parse_master(text: str) -> list[tuple[int, int, str]]:
    """Return (height, bandwidth, uri) from master playlists, without I/O."""
    rows = _lines(text)
    variants = []
    for index, row in enumerate(rows):
        if row.startswith("#EXT-X-STREAM-INF:"):
            if index + 1 >= len(rows) or rows[index + 1].startswith("#"):
                raise DownloadError("HLS variant missing URI")
            resolution = STREAM_RE.search(row)
            bandwidth = BANDWIDTH_RE.search(row)
            height = int(resolution.group(2)) if resolution else 0
            speed = int(bandwidth.group(1)) if bandwidth else 0
            variants.append((height, speed, rows[index + 1]))
    if len(variants) > 32:
        raise DownloadError("too many HLS variants")
    return variants


def choose_variant(variants: list[tuple[int, int, str]]) -> str:
    if not variants:
        raise DownloadError("empty HLS master")
    suitable = [variant for variant in variants if variant[0] >= 720]
    if suitable:
        return min(suitable, key=lambda row: (row[0], row[1], row[2]))[2]
    return max(variants, key=lambda row: (row[0], -row[1], row[2]))[2]


def parse_media(text: str) -> list[tuple[float, str]]:
    rows = _lines(text)
    if any(row.startswith("#EXT-X-STREAM-INF:") for row in rows):
        raise DownloadError("nested HLS masters are unsupported")
    segments: list[tuple[float, str]] = []
    for index, row in enumerate(rows):
        if row.startswith("#EXTINF:"):
            if index + 1 >= len(rows) or rows[index + 1].startswith("#"):
                raise DownloadError("HLS segment missing URI")
            try:
                duration = float(row.split(":", 1)[1].split(",", 1)[0])
            except ValueError as exc:
                raise DownloadError("invalid HLS duration") from exc
            if not (0 < duration < math.inf):
                raise DownloadError("HLS duration out of bounds")
            segments.append((duration, rows[index + 1]))
    if not segments or len(segments) > MAX_SEGMENTS:
        raise DownloadError("HLS segment count out of bounds")
    return segments


async def _validated(
    url: str, base: str, allowed_hosts: frozenset[str], resolver
) -> str:
    address = urljoin(base, url)
    parsed = urlsplit(address)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise UnsafeMediaURL("HLS URI host or scheme not allowed")
    await validate_public_url(address, resolver=resolver)
    return address


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    referer: str | None,
    resolver,
    own_client: bool,
    max_bytes: int,
) -> bytes:
    addresses: list[str] = []
    await validate_public_url(url, resolver=resolver, validated_addresses=addresses)
    headers = {"User-Agent": USER_AGENT}
    if referer:
        if "\r" in referer or "\n" in referer or urlsplit(referer).scheme != "https":
            raise DownloadError("invalid HLS Referer")
        headers["Referer"] = referer
    body = bytearray()
    try:
        async with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            peer_stream = response.extensions.get("network_stream")
            if peer_stream is None:
                if own_client:
                    raise DownloadError("HLS connected peer unavailable")
            else:
                try:
                    peer = str(peer_stream.get_extra_info("server_addr")[0])
                except (TypeError, KeyError, AttributeError, IndexError) as exc:
                    raise DownloadError("HLS connected peer could not be inspected") from exc
                if not _is_public(peer) or peer not in addresses:
                    raise DownloadError("HLS peer changed after DNS resolution")
            if response.status_code != 200:
                raise DownloadError("HLS request blocked or redirected")
            declared = response.headers.get("content-length")
            if declared:
                try:
                    size = int(declared)
                except ValueError as exc:
                    raise DownloadError("invalid HLS Content-Length") from exc
                if size > max_bytes:
                    raise DownloadError("HLS segment exceeds byte cap")
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise DownloadError("HLS streamed bytes exceed cap")
    except httpx.HTTPError as exc:
        raise DownloadError("HLS transport failure") from exc
    return bytes(body)


async def download_hls(
    url: str,
    target_dir: Path,
    *,
    referer: str | None = None,
    allowed_hosts: tuple[str, ...] = (),
    max_duration: float = 20.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    client: httpx.AsyncClient | None = None,
    resolver=None,
) -> Path:
    """Download bounded HLS segments and let FFmpeg read *local* media only."""
    if not 0 < max_duration <= MAX_HLS_DURATION:
        raise ValueError("HLS max_duration must be in (0, 20]")
    if not 0 < max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("invalid HLS max_bytes")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeMediaURL("HLS playlist must be HTTPS")
    hosts = frozenset((parsed.hostname, *allowed_hosts))
    target_dir.mkdir(parents=True, exist_ok=True)
    own = client is None
    connection = client or httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10, read=20),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )
    output = target_dir / ("clipoteca-hls-" + os.urandom(8).hex() + ".mp4")
    try:
        async with asyncio.timeout(180):
            async with asyncio.TaskGroup() as group:
                group.create_task(_validated(url, url, hosts, resolver))
            with tempfile.TemporaryDirectory(prefix="clipoteca-hls-", dir=target_dir) as tmp:
                stage = Path(tmp)
                master = (
                    await _fetch(
                        connection, url, referer=referer, resolver=resolver,
                        own_client=own, max_bytes=MAX_PLAYLIST_BYTES,
                    )
                ).decode("utf-8", errors="replace")
                variants = parse_master(master)
                media_url = url
                media_text = master
                if variants:
                    # Validate *all* variant hosts before choosing a stream.
                    urls = [
                        await _validated(uri, url, hosts, resolver)
                        for _height, _speed, uri in variants
                    ]
                    chosen_uri = choose_variant(variants)
                    selected_index = next(
                        index for index, row in enumerate(variants)
                        if row[2] == chosen_uri
                    )
                    media_url = urls[selected_index]
                    media_text = (
                        await _fetch(
                            connection, media_url, referer=referer, resolver=resolver,
                            own_client=own, max_bytes=MAX_PLAYLIST_BYTES,
                        )
                    ).decode("utf-8", errors="replace")
                segments = parse_media(media_text)
                # A malicious URI anywhere in the selected playlist is rejected,
                # including after our eventual 20-second cut.
                validated = [
                    await _validated(uri, media_url, hosts, resolver)
                    for _duration, uri in segments
                ]
                used = 0
                elapsed = 0.0
                local_playlist = ["#EXTM3U", "#EXT-X-VERSION:3"]
                for index, ((duration, _uri), segment_url) in enumerate(
                    zip(segments, validated, strict=True)
                ):
                    if elapsed >= max_duration:
                        break
                    remaining = max_bytes - used
                    if remaining <= 0:
                        raise DownloadError("HLS aggregate byte cap reached")
                    payload = await _fetch(
                        connection, segment_url, referer=referer,
                        resolver=resolver, own_client=own, max_bytes=remaining,
                    )
                    if not payload:
                        raise DownloadError("HLS empty media segment")
                    used += len(payload)
                    suffix = ".ts"
                    filename = f"part-{index:03d}{suffix}"
                    (stage / filename).write_bytes(payload)
                    local_playlist.extend([f"#EXTINF:{duration:.4f},", filename])
                    elapsed += duration
                local_playlist.append("#EXT-X-ENDLIST")
                playlist_path = stage / "local.m3u8"
                playlist_path.write_text("\n".join(local_playlist) + "\n", encoding="utf-8")
                command = [
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-y", "-protocol_whitelist", "file,crypto",
                    "-allowed_extensions", "ALL", "-i", str(playlist_path),
                    "-t", str(max_duration), "-fs", str(max_bytes),
                    "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", str(output),
                ]
                try:
                    await asyncio.to_thread(
                        subprocess.run,
                        command,
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=180,
                        env=safe_media_env(),
                    )
                except (subprocess.SubprocessError, OSError) as exc:
                    raise DownloadError("HLS FFmpeg remux failed") from exc
                if not output.is_file() or not 0 < output.stat().st_size <= max_bytes:
                    raise DownloadError("HLS output violated byte cap")
                return output
    except TimeoutError as exc:
        raise DownloadError("HLS deadline exceeded") from exc
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        if own:
            await connection.aclose()
