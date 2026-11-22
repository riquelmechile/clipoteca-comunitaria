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
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import USER_AGENT
from ..media.process_env import safe_media_env
from .download import DownloadError, MAX_DOWNLOAD_BYTES
from .pinning import matches_validated_ip
from .ssrf import UnsafeMediaURL, _is_public, validate_public_url

HLS_MIME_TYPES = frozenset(
    {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "audio/mpegurl",
    }
)


@lru_cache(maxsize=1)
def ffmpeg_hls_extension_capable() -> bool:
    """Probe the HLS demuxer option once per process, not FFmpeg's version string."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "demuxer=hls"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=safe_media_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "allowed_segment_extensions" in (
        result.stdout + result.stderr
    )


MAX_PLAYLIST_BYTES = 4 * 1024 * 1024
MAX_HLS_DURATION = 20.0
STREAM_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")
BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)")


def _lines(playlist: str) -> list[str]:
    if not playlist.lstrip().startswith("#EXTM3U"):
        raise DownloadError("invalid HLS playlist")
    rows = [row.strip() for row in playlist.splitlines() if row.strip()]
    if len(rows) > 20000:
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
    if not segments:
        raise DownloadError("HLS has no playable segments")
    return segments


async def _validated(url: str, base: str, allowed_hosts: frozenset[str], resolver) -> str:
    address = urljoin(base, url)
    parsed = urlsplit(address)
    host = parsed.hostname or ""
    # Exact hosts or explicitly scoped suffixes, e.g. ".rtbcdn.ru"; never a
    # substring match. Even an allowed hostname still needs public DNS.
    allowed = host in allowed_hosts or any(
        entry.startswith(".") and host.endswith(entry) and host != entry[1:]
        for entry in allowed_hosts
    )
    if parsed.scheme != "https" or not allowed:
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
    probe_prefix: bool = False,
) -> bytes:
    """Connect to the previously validated public IP, not a second DNS answer.

    HTTPS SNI and certificate verification retain the original hostname.
    MockTransport is allowed for isolated tests; all other transports must
    expose a matching connected peer. A foreign injected client does not
    bypass validation or pinning.
    """
    addresses: list[str] = []
    await validate_public_url(url, resolver=resolver, validated_addresses=addresses)
    if not addresses:
        raise DownloadError("HLS host has no validated address")
    host = urlsplit(url).hostname
    assert host is not None
    headers = {"User-Agent": USER_AGENT, "Host": host}
    if referer:
        if "\r" in referer or "\n" in referer or urlsplit(referer).scheme != "https":
            raise DownloadError("invalid HLS Referer")
        headers["Referer"] = referer
    last_transport_error: httpx.HTTPError | None = None
    for chosen_ip in addresses:
        # Never re-resolve on connect. Only previously validated public addresses
        # may be used, retaining the original hostname for TLS SNI and Host.
        pinned_url = httpx.URL(url).copy_with(host=chosen_ip)
        body = bytearray()
        try:
            async with client.stream(
                "GET",
                pinned_url,
                headers=headers,
                follow_redirects=False,
                extensions={"sni_hostname": host},
            ) as response:
                peer_stream = response.extensions.get("network_stream")
                if peer_stream is None:
                    if not isinstance(getattr(client, "_transport", None), httpx.MockTransport):
                        raise DownloadError("HLS connected peer unavailable")
                else:
                    try:
                        peer = str(peer_stream.get_extra_info("server_addr")[0])
                    except (TypeError, KeyError, AttributeError, IndexError) as exc:
                        raise DownloadError("HLS connected peer could not be inspected") from exc
                    if not _is_public(peer) or not matches_validated_ip(
                        peer, (chosen_ip,), normalize_ipv6=True
                    ):
                        # A mismatched/private peer is a security failure, never
                        # retried against another address or silently accepted.
                        raise DownloadError("HLS connected peer mismatch after IP pinning")
                if response.status_code != 200:
                    # A redirect is never followed to a new host.
                    if response.status_code in {301, 302, 303, 307, 308}:
                        raise DownloadError("HLS request blocked or redirected")
                    # Non-redirect HTTP failures can be IP-specific.
                    if chosen_ip != addresses[-1]:
                        continue
                    raise DownloadError("HLS request blocked or redirected")
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        size = int(declared)
                    except ValueError as exc:
                        raise DownloadError("invalid HLS Content-Length") from exc
                    if size > max_bytes and not probe_prefix:
                        raise DownloadError("HLS segment exceeds byte cap")
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if probe_prefix:
                        body.extend(chunk[: max_bytes - len(body)])
                        if len(body) >= max_bytes:
                            break
                    else:
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise DownloadError("HLS streamed bytes exceed cap")
            return bytes(body)
        except httpx.HTTPError as exc:
            last_transport_error = exc
    raise DownloadError(
        "HLS transport failure for all validated addresses"
    ) from last_transport_error


async def download_hls(
    url: str,
    target_dir: Path,
    *,
    referer: str | None = None,
    allowed_hosts: tuple[str, ...] = (),
    max_duration: float = 20.0,
    start: float = 0.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    client: httpx.AsyncClient | None = None,
    resolver=None,
) -> Path:
    """Download bounded HLS segments and let FFmpeg read *local* media only."""
    if not 0 < max_duration <= MAX_HLS_DURATION:
        raise ValueError("HLS max_duration must be in (0, 20]")
    if not 0 <= start < math.inf:
        raise ValueError("HLS start must be a finite nonnegative offset")
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
            await _validated(url, url, hosts, resolver)
            with tempfile.TemporaryDirectory(prefix="clipoteca-hls-", dir=target_dir) as tmp:
                stage = Path(tmp)
                master = (
                    await _fetch(
                        connection,
                        url,
                        referer=referer,
                        resolver=resolver,
                        own_client=own,
                        max_bytes=MAX_PLAYLIST_BYTES,
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
                        index for index, row in enumerate(variants) if row[2] == chosen_uri
                    )
                    media_url = urls[selected_index]
                    media_text = (
                        await _fetch(
                            connection,
                            media_url,
                            referer=referer,
                            resolver=resolver,
                            own_client=own,
                            max_bytes=MAX_PLAYLIST_BYTES,
                        )
                    ).decode("utf-8", errors="replace")
                segments = parse_media(media_text)
                # Select only a window from the start offset. Long VOD may
                # contain hundreds of segments; URI validation/download is
                # limited to exactly those intersecting the requested cut.
                current = 0.0
                selected: list[tuple[float, str]] = []
                first_start = 0.0
                for duration, uri in segments:
                    segment_end = current + duration
                    if segment_end > start and current < start + max_duration:
                        if not selected:
                            first_start = current
                        selected.append((duration, uri))
                    current = segment_end
                    if current >= start + max_duration:
                        break
                if not selected:
                    raise DownloadError("HLS requested window is outside playlist")
                # Validate all selected URIs before downloading any media.
                validated = [
                    await _validated(uri, media_url, hosts, resolver) for duration, uri in selected
                ]
                target_duration = max(1, math.ceil(max(duration for duration, _uri in selected)))
                local_playlist = [
                    "#EXTM3U",
                    "#EXT-X-VERSION:3",
                    f"#EXT-X-TARGETDURATION:{target_duration}",
                    "#EXT-X-MEDIA-SEQUENCE:0",
                ]
                used = 0
                for index, ((duration, _uri), segment_url) in enumerate(
                    zip(selected, validated, strict=True)
                ):
                    remaining = max_bytes - used
                    if remaining <= 0:
                        raise DownloadError("HLS aggregate byte cap reached")
                    payload = await _fetch(
                        connection,
                        segment_url,
                        referer=referer,
                        resolver=resolver,
                        own_client=own,
                        max_bytes=remaining,
                    )
                    if not payload:
                        raise DownloadError("HLS empty media segment")
                    used += len(payload)
                    filename = f"part-{index:03d}.ts"
                    (stage / filename).write_bytes(payload)
                    local_playlist.extend([f"#EXTINF:{duration:.4f},", filename])
                local_playlist.append("#EXT-X-ENDLIST")
                playlist_path = stage / "local.m3u8"
                playlist_path.write_text("\n".join(local_playlist) + "\n", encoding="utf-8")
                if not ffmpeg_hls_extension_capable():
                    raise DownloadError(
                        "FFmpeg HLS requires -allowed_segment_extensions ts "
                        "(e.g. FFmpeg 6.1.3+, 7.0.3+, 7.1.2+ or 8.0+)"
                    )
                command = [
                    "ffmpeg",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-protocol_whitelist",
                    "file,crypto",
                    "-allowed_segment_extensions",
                    "ts",
                    "-i",
                    str(playlist_path),
                    "-ss",
                    str(max(0.0, start - first_start)),
                    "-t",
                    str(max_duration),
                    "-fs",
                    str(max_bytes),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output),
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
                except (subprocess.CalledProcessError, OSError) as exc:
                    if "allowed_segment_extensions" in str(getattr(exc, "stderr", "")):
                        raise DownloadError(
                            "FFmpeg lacks secure HLS segment extension option"
                        ) from exc
                    raise DownloadError("HLS FFmpeg remux failed") from exc
                except subprocess.SubprocessError as exc:
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


def _hls_probe_magic(data: bytes) -> bool:
    """Recognize MPEG-TS sync packets or an ISO BMFF media segment."""
    return (len(data) >= 376 and data[0] == 0x47 and data[188] == 0x47) or (
        len(data) >= 12 and data[4:8] == b"ftyp"
    )


async def download_hls_probe(
    url: str,
    target_dir: Path,
    *,
    referer: str | None = None,
    allowed_hosts: tuple[str, ...] = (),
    max_duration: float = 20.0,
    max_bytes: int = 32 * 1024 * 1024,
    client: httpx.AsyncClient | None = None,
    resolver=None,
) -> Path:
    """Opt-in smoke: verify HLS bytes to a cap without publishing or remuxing.

    Only validated playlist and segment hosts are touched. Capping a
    single large segment is evidence of streaming, not a complete ingest.
    """
    if not 0 < max_duration <= MAX_HLS_DURATION:
        raise ValueError("invalid HLS smoke duration")
    if not 376 <= max_bytes <= MAX_DOWNLOAD_BYTES:
        raise ValueError("invalid HLS smoke byte cap")
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
    target = target_dir / ("clipoteca-smoke-hls-" + os.urandom(8).hex() + ".ts")
    successful = False
    try:
        async with asyncio.timeout(180):
            await _validated(url, url, hosts, resolver)
            master = (
                await _fetch(
                    connection,
                    url,
                    referer=referer,
                    resolver=resolver,
                    own_client=own,
                    max_bytes=MAX_PLAYLIST_BYTES,
                )
            ).decode("utf-8", errors="replace")
            variants = parse_master(master)
            media_url = url
            media_text = master
            if variants:
                choices = [await _validated(uri, url, hosts, resolver) for _, _, uri in variants]
                chosen = choose_variant(variants)
                media_url = choices[
                    next(i for i, (_, _, uri) in enumerate(variants) if uri == chosen)
                ]
                media_text = (
                    await _fetch(
                        connection,
                        media_url,
                        referer=referer,
                        resolver=resolver,
                        own_client=own,
                        max_bytes=MAX_PLAYLIST_BYTES,
                    )
                ).decode("utf-8", errors="replace")
            segments = parse_media(media_text)
            selected: list[tuple[float, str]] = []
            duration = 0.0
            for seconds, uri in segments:
                if duration >= max_duration:
                    break
                selected.append((seconds, await _validated(uri, media_url, hosts, resolver)))
                duration += seconds
            total = 0
            with target.open("wb") as output:
                for _seconds, segment in selected:
                    remaining = max_bytes - total
                    if remaining <= 0:
                        break
                    body = await _fetch(
                        connection,
                        segment,
                        referer=referer,
                        resolver=resolver,
                        own_client=own,
                        max_bytes=remaining,
                        probe_prefix=True,
                    )
                    if not body or (total == 0 and not _hls_probe_magic(body)):
                        raise DownloadError("invalid HLS smoke media signature")
                    total += len(body)
                    output.write(body)
                    if total >= max_bytes:
                        break
            if total == 0:
                raise DownloadError("empty HLS smoke download")
            successful = True
            return target
    finally:
        if not successful:
            target.unlink(missing_ok=True)
        if own:
            await connection.aclose()
