"""Pure IP comparison shared by media fetchers; no DNS resolution or I/O."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Iterable


def matches_validated_ip(
    peer: str, addresses: Iterable[str], *, normalize_ipv6: bool = False
) -> bool:
    """Match a DNS-validated peer; preserve legacy download matching by default."""
    if not normalize_ipv6:
        return peer in addresses
    try:
        parsed = ip_address(peer)
        return any(parsed == ip_address(address) for address in addresses)
    except ValueError:
        return False
