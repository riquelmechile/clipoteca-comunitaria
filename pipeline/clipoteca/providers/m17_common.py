"""Shared, deliberately small parsing and URL gates for M17 providers."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from .base import ProviderError


def json_attributes(document: str, attribute: str) -> list[object]:
    pattern = re.compile(rf"{re.escape(attribute)}\s*=\s*(['\"])(.*?)\1", re.I | re.S)
    values: list[object] = []
    for match in pattern.finditer(document):
        for candidate in (match.group(2), html.unescape(match.group(2))):
            try:
                values.append(json.loads(candidate))
                break
            except (TypeError, ValueError):
                continue
    return values


def embedded_json(document: str, marker: str) -> list[object]:
    """Decode JSON objects immediately following a trusted marker."""
    decoder = json.JSONDecoder()
    values: list[object] = []
    for match in re.finditer(re.escape(marker), document):
        tail = document[match.end() :].lstrip(" \t\r\n\"':=")
        try:
            value, _end = decoder.raw_decode(tail)
        except ValueError:
            continue
        values.append(value)
    return values


def walk(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def duration(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0 < parsed < float("inf") else None


def allowed_https(url: str, roots: tuple[str, ...]) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and any(host == root or host.endswith("." + root) for root in roots)
    )


async def response_json(response, provider: str) -> dict:
    if response.status_code in {301, 302, 303, 307, 308}:
        raise ProviderError(f"{provider} redirect rejected")
    if response.status_code >= 400:
        raise ProviderError(f"{provider} HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(f"{provider} returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderError(f"{provider} payload must be an object")
    return payload
