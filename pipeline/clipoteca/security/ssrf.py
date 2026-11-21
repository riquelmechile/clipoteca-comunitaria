"""Reject unsafe URL targets, including all private DNS answers and redirects."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit


class UnsafeMediaURL(ValueError):
    pass


def _is_public(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_global
    except ValueError:
        return False


async def _default_resolver(host: str) -> list[str]:
    try:
        found = await asyncio.get_running_loop().getaddrinfo(
            host,
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UnsafeMediaURL("DNS resolution failed") from exc
    return list({entry[4][0] for entry in found})


async def validate_public_url(
    url: str,
    *,
    resolver: Callable[[str], Iterable[str]] | Callable | None = None,
    validated_addresses: list[str] | None = None,
) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise UnsafeMediaURL("invalid media URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or host.lower() in {"localhost", "metadata.google.internal"}
        or host.lower().endswith((".localhost", ".local", ".internal", ".test"))
    ):
        raise UnsafeMediaURL("unsafe URL scheme, host or port")
    if any(c in host for c in ("/", "\\", "%")):
        raise UnsafeMediaURL("unsafe hostname")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal:
        if not _is_public(host):
            raise UnsafeMediaURL("private or special-use IP address")
        if validated_addresses is not None:
            validated_addresses.append(str(literal))
    else:
        fn = resolver or _default_resolver
        addresses = fn(host)
        if inspect.isawaitable(addresses):
            addresses = await addresses
        addresses = list(addresses)
        if not addresses or not all(_is_public(str(addr)) for addr in addresses):
            raise UnsafeMediaURL("DNS returned private, special-use or empty address")
        if validated_addresses is not None:
            validated_addresses.extend(str(address) for address in addresses)
    return url
