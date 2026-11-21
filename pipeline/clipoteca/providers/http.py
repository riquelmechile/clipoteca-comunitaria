"""Small error-typed JSON fetch shared by provider adapters."""

from __future__ import annotations

import asyncio

import httpx

from ..config import USER_AGENT
from .base import AuthRequired, ProviderError, ProviderTimeout, RateLimited


def provider_client() -> httpx.AsyncClient:
    """Use the same project identity for all live provider HTTP clients."""
    return httpx.AsyncClient(timeout=15, follow_redirects=False, headers={"User-Agent": USER_AGENT})


async def fetch_json(client: httpx.AsyncClient, url: str, *, params=None, headers=None) -> dict:
    """Bounded 429 retry with Retry-After. 403 is auth only with credentials."""
    request_headers = httpx.Headers(headers or {})
    request_headers.setdefault("User-Agent", USER_AGENT)
    response = None
    for attempt in range(3):
        try:
            response = await client.get(url, params=params, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("provider timeout") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider request failed") from exc
        if response.status_code != 429:
            break
        if attempt == 2:
            raise RateLimited("provider rate limit")
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = max(0.0, float(retry_after)) if retry_after else 0.25 * (2**attempt)
        except ValueError:
            delay = 0.25 * (2**attempt)
        await asyncio.sleep(min(delay, 1.0))
    assert response is not None
    credentialed = bool(
        headers
        and any(
            value
            for key, value in headers.items()
            if key.casefold() in {"authorization", "access-token", "x-api-key", "api-key"}
        )
    )
    if isinstance(params, dict):
        credentialed = credentialed or bool(
            params.get("key") or params.get("api_key") or params.get("access_token")
        )
    if response.status_code == 401 or (response.status_code == 403 and credentialed):
        raise AuthRequired("provider API authorization required")
    if response.status_code == 400 and "pixabay.com" in url:
        body = response.text.casefold()
        if "key" in body and ("invalid" in body or "missing" in body):
            raise AuthRequired("Pixabay API key invalid")
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
