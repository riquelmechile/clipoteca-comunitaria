"""Small error-typed JSON fetch shared by provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

import httpx

from ..config import USER_AGENT
from ..security.ssrf import UnsafeMediaURL, _is_public, validate_public_url
from .base import AuthRequired, ProviderError, ProviderTimeout, RateLimited

MAX_PROVIDER_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderPayloadError(ProviderError):
    """Unusable JSON shape or encoding, eligible for a provider HTML fallback."""


def provider_client(**kwargs) -> httpx.AsyncClient:
    """Use the same project identity for all live provider HTTP clients."""
    return httpx.AsyncClient(
        timeout=15,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        **kwargs,
    )


def _is_mock_transport(client: httpx.AsyncClient) -> bool:
    """Explicit, exact test transport type; never infer trust from a class name."""
    return type(client._transport) is httpx.MockTransport  # noqa: SLF001


async def fetch_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    method: str = "GET",
    params=None,
    data=None,
    headers=None,
    max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
    timeout_seconds: float = 15,
    resolver: Callable[[str], Iterable[str]] | Callable | None = None,
) -> httpx.Response:
    """Stream one bounded response and pin its peer to public DNS answers."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    request_headers = httpx.Headers(headers or {})
    request_headers.setdefault("User-Agent", USER_AGENT)
    validated_addresses: list[str] = []
    mock_transport = _is_mock_transport(client)
    try:
        if not mock_transport:
            await validate_public_url(
                url, resolver=resolver, validated_addresses=validated_addresses
            )
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                method,
                url,
                params=params,
                data=data,
                headers=request_headers,
                follow_redirects=False,
            ) as response:
                stream = response.extensions.get("network_stream")
                if stream is not None and validated_addresses:
                    try:
                        address = stream.get_extra_info("server_addr")
                        peer = str(address[0])
                    except (TypeError, KeyError, AttributeError, IndexError) as exc:
                        raise ProviderError("provider peer address unavailable") from exc
                    if not _is_public(peer) or peer not in validated_addresses:
                        raise ProviderError("provider peer changed after DNS validation")
                elif not mock_transport:
                    raise ProviderError("provider peer address unavailable")
                length = response.headers.get("content-length")
                if length:
                    try:
                        declared = int(length)
                    except ValueError as exc:
                        raise ProviderError("provider returned invalid Content-Length") from exc
                    if declared > max_bytes:
                        raise ProviderError("provider response exceeds size limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise ProviderError("provider response exceeds size limit")
                # aiter_bytes() has already decoded any Content-Encoding.
                # Preserve the decompressed-size cap, but do not make httpx
                # decode the reconstructed response a second time.
                decoded_headers = response.headers.copy()
                decoded_headers.pop("content-encoding", None)
                decoded_headers.pop("content-length", None)
                return httpx.Response(
                    response.status_code,
                    headers=decoded_headers,
                    content=bytes(body),
                    request=response.request,
                )
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ProviderTimeout("provider timeout") from exc
    except UnsafeMediaURL as exc:
        raise ProviderError("provider URL rejected") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("provider request failed") from exc


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    **kwargs,
) -> str:
    """Return bounded text while rejecting redirects and HTTP errors."""
    response = await fetch_response(client, url, **kwargs)
    if response.is_redirect:
        raise ProviderError("provider redirect rejected")
    if response.status_code >= 400:
        raise ProviderError(f"provider HTTP {response.status_code}")
    return response.text


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params=None,
    headers=None,
    detail_statuses: frozenset[int] = frozenset(),
    before_request=None,
    retry_statuses=(429,),
    max_retry_after_seconds=1.0,
    method: str = "GET",
    data=None,
    max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
    timeout_seconds: float = 15,
) -> dict:
    """Bounded retry with Retry-After and optional pre-request pacing.

    Opt-in HTTP errors are classified by status, never untrusted JSON detail.
    Other providers retain bounded retries and credential-aware handling.
    """
    request_headers = httpx.Headers(headers or {})
    request_headers.setdefault("User-Agent", USER_AGENT)
    response = None
    for attempt in range(3):
        if before_request is not None:
            await before_request()
        response = await fetch_response(
            client,
            url,
            method=method,
            params=params,
            data=data,
            headers=request_headers,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        if response.status_code not in retry_statuses:
            break
        if attempt == 2:
            if response.status_code == 429:
                raise RateLimited("provider rate limit")
            raise ProviderError(f"provider HTTP {response.status_code}")
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = max(0.0, float(retry_after)) if retry_after else 0.25 * (2**attempt)
        except ValueError:
            delay = 0.25 * (2**attempt)
        await asyncio.sleep(min(delay, max_retry_after_seconds))
    assert response is not None
    if response.status_code in detail_statuses and response.status_code in {403, 429, 451}:
        # Classify opted-in HTTP errors by status alone; never inspect untrusted JSON.
        # 429 reaches this point only after bounded Retry-After retries.
        raise RateLimited("provider request blocked or rate limited")
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
        raise ProviderPayloadError("provider returned non-JSON content")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderPayloadError("provider returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderPayloadError("provider payload must be an object")
    return payload
