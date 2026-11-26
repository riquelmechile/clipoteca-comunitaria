"""Wait for submitted public contributions before assigning scene material."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx

from .script import ScriptError

MAX_WAIT_SECONDS = 900
POLL_SECONDS = 10


async def resolve_pending_jobs(
    jobs: list[dict],
    estimates: list[dict],
    api: str,
    *,
    max_seconds: float = MAX_WAIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Wait once per distinct ref, with one deadline for the entire plan."""
    origin = urlsplit(api)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise ScriptError("media API must be a trusted HTTPS origin")
    refs = {
        job["media"]["ref"]
        for job in jobs
        if job.get("source_kind") == "contribution"
    }
    if not refs:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(MAX_WAIT_SECONDS, max(0, max_seconds))
    failures: dict[str, str] = {}

    async def check(client: httpx.AsyncClient, ref: str) -> tuple[str, str | None]:
        try:
            response = await client.get(api.rstrip("/") + "/v1/media/" + ref)
            if response.status_code == 404:
                return ref, "media_not_found"
            if response.status_code != 200:
                return ref, None
            row = response.json()
            if not isinstance(row, dict):
                return ref, None
            if row.get("media_id") != ref:
                return ref, None
            if row.get("status") == "registered":
                return ref, ""
            if row.get("status") == "rejected":
                return ref, "rejected: " + str(row.get("reason") or "no reason")[:300]
        except (httpx.HTTPError, ValueError):
            pass
        return ref, None

    async with httpx.AsyncClient(
        timeout=15, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        while refs and loop.time() < deadline:
            try:
                async with asyncio.timeout_at(deadline):
                    rows = await asyncio.gather(*(check(client, ref) for ref in sorted(refs)))
            except TimeoutError:
                break
            for ref, reason in rows:
                if reason is not None:
                    refs.remove(ref)
                    if reason:
                        failures[ref] = reason
            if refs:
                await asyncio.sleep(min(poll_seconds, max(0, deadline - loop.time())))
    failures.update({ref: "registration_timeout (15 min maximum)" for ref in refs})
    by_id = {row["id"]: row for row in estimates}
    for job in jobs:
        media = job.get("media")
        if not media or media["ref"] not in failures:
            continue
        ref = media["ref"]
        reason = "media " + ref + ": " + failures[ref]
        queries = job.get("queries", {})
        if not (queries.get("es") and queries.get("en") and job.get("prompt")):
            raise ScriptError(reason)
        job.pop("media")
        job.pop("source_kind")
        job.pop("on_error", None)
        job["media_fallback_reason"] = reason
        by_id[job["id"]].pop("source_kind", None)
