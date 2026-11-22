"""Pin outbound HTTPS connections to public IPs validated by the caller.

The URL used for the TCP connection contains a validated IP; the HTTP Host
header and the TLS SNI/certificate hostname remain the original DNS name.
A fresh HTTP transport avoids reusing an IP-keyed TLS connection for a
different hostname sharing the same CDN IP.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import copy
from ipaddress import ip_address
from typing import Iterable

import httpx

from .ssrf import _is_public


class PinnedPeerError(ValueError):
    """No verified TCP peer was observed; never emit a target URL or IP."""


def _normalized_ip(value: str):
    parsed = ip_address(value.split("%", 1)[0])
    return parsed.ipv4_mapped or parsed if hasattr(parsed, "ipv4_mapped") else parsed


def matches_validated_ip(
    peer: str, addresses: Iterable[str], *, normalize_ipv6: bool = False
) -> bool:
    """Match public peers across equivalent IPv6 / IPv4-mapped spellings."""
    if not normalize_ipv6:
        return peer in addresses
    try:
        parsed = _normalized_ip(peer)
        return any(parsed == _normalized_ip(address) for address in addresses)
    except ValueError:
        return False


def secure_pinned_transport() -> httpx.AsyncHTTPTransport:
    """Isolated verified TLS pool; never use proxies or disable certificate checks."""
    return httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)


def _verify_peer(
    response: httpx.Response,
    chosen: str | None,
    *,
    is_mock: bool,
) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        if not is_mock:
            raise PinnedPeerError("connected peer address unavailable")
        return
    try:
        addr = stream.get_extra_info("server_addr")
        peer = str(addr[0])
    except (TypeError, AttributeError, KeyError, IndexError) as exc:
        raise PinnedPeerError("connected peer address unavailable") from exc
    if not _is_public(peer):
        raise PinnedPeerError("connected peer mismatch after IP pinning")
    if chosen is not None and not matches_validated_ip(peer, (chosen,), normalize_ipv6=True):
        raise PinnedPeerError("connected peer mismatch after IP pinning")


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Use a supplied transport without closing it or altering its cookie jar."""

    def __init__(self, transport: httpx.AsyncBaseTransport):
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        # The original client owns this transport.
        return None


@asynccontextmanager
async def stream_pinned(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    addresses: Iterable[str],
    params=None,
    data=None,
    headers=None,
):
    """Yield one response from a validated address, trying other IPs on connect errors.

    Exact MockTransport keeps the original URL and bypasses real DNS for tests.
    Foreign custom transports must still reveal a public matching peer.
    Redirects remain disabled and must be validated as separate requests.
    """
    candidates = list(dict.fromkeys(str(addr) for addr in addresses))
    transport = getattr(client, "_transport", None)
    is_mock = type(transport) is httpx.MockTransport
    if is_mock:
        async with client.stream(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            follow_redirects=False,
        ) as response:
            _verify_peer(response, candidates[0] if candidates else None, is_mock=True)
            yield response
        return

    if not candidates:
        raise PinnedPeerError("connected peer address unavailable")
    if not all(_is_public(addr) for addr in candidates):
        raise PinnedPeerError("connected peer mismatch after IP pinning")

    last_connect_error: httpx.HTTPError | None = None
    for chosen in candidates:
        request = client.build_request(method, url, params=params, data=data, headers=headers)
        # Preserve metadata for cookie extraction and adapter-visible URLs.
        # The wire request can still target a verified IP.
        original_request = copy(request)
        hostname = request.url.host
        if not hostname:
            raise PinnedPeerError("connected peer address unavailable")
        # build_request populates cookies and query for the ORIGINAL origin.
        # Only the target TCP endpoint changes; TLS checks the original name.
        request.url = request.url.copy_with(host=chosen)
        request.headers["host"] = f"[{hostname}]" if ":" in hostname else hostname
        request.extensions["sni_hostname"] = hostname

        # Isolate the request even for borrowed transports: client.send()
        # would otherwise store host-only cookies under the pinned IP before
        # their origin can be restored, corrupting the original cookie jar.
        # The borrowed adapter never closes the caller's transport.
        pinned_transport = (
            secure_pinned_transport()
            if type(transport) is httpx.AsyncHTTPTransport
            else _BorrowedTransport(transport)
        )
        async with httpx.AsyncClient(
            transport=pinned_transport,
            trust_env=False,
            follow_redirects=False,
            timeout=client.timeout,
        ) as pinned_client:
            try:
                response = await pinned_client.send(request, stream=True, follow_redirects=False)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_connect_error = exc
                continue
            try:
                _verify_peer(response, chosen, is_mock=False)
                # Preserve the original URL for relative redirects, logs and
                # Set-Cookie processing, leaving the wire request IP-pinned.
                response.request = original_request
                client.cookies.extract_cookies(response)
                yield response
            finally:
                await response.aclose()
            return
    if last_connect_error is not None:
        raise last_connect_error
    raise PinnedPeerError("connected peer address unavailable")
