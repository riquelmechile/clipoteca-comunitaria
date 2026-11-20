"""Small error-typed JSON fetch shared by provider adapters."""

from __future__ import annotations

import httpx

from .base import AuthRequired, ProviderError, ProviderTimeout, RateLimited


async def fetch_json(client: httpx.AsyncClient, url: str, *, params=None, headers=None) -> dict:
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise ProviderTimeout("provider timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("provider request failed") from exc
    if response.status_code in (401, 403):
        raise AuthRequired("provider API authorization required")
    if response.status_code == 429:
        raise RateLimited("provider rate limit")
    if response.status_code >= 400:
        raise ProviderError(f"provider HTTP {response.status_code}")
    if "json" not in response.headers.get("content-type", ""):
        raise ProviderError("provider returned non-JSON content")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("provider returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderError("provider payload must be an object")
    return payload
