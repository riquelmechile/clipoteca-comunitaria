"""Live provider smoke with bounded network; never logs credentials or media URLs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from clipoteca.providers.base import (
    AuthRequired,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    SearchLimits,
)
from clipoteca.security.download import DownloadError
from clipoteca.sources import SOURCES
from clipoteca.candidate_probe import candidates_smoke

TERMS = {"es": "lluvia", "en": "rain", "zh": "\u4e0b\u96e8", "ru": "\u0434\u043e\u0436\u0434\u044c"}
REPO = "riquelmechile/clipoteca-comunitaria"


# Our own typed errors may be recorded, but only with narrow ASCII grammar.
# No upstream exception body, URL, header or token reaches the artifact.
_SAFE_ERROR = re.compile(r"[A-Za-z0-9 ._:()-]{1,120}\Z")


def _diagnostic(row: dict, exc: Exception) -> None:
    row["error_type"] = type(exc).__name__
    if not isinstance(exc, (ProviderError, AuthRequired, RateLimited, DownloadError)):
        return
    message = str(exc)
    lower = message.casefold()
    forbidden = (
        "http:",
        "https:",
        "bearer ",
        "cookie",
        "api_key",
        "access_token",
        "authorization",
        "password",
        "secret",
        "signature",
    )
    if _SAFE_ERROR.fullmatch(message) and not any(x in lower for x in forbidden):
        row["error_detail"] = message
        status = re.search(r"HTTP ([1-5][0-9]{2})", message)
        if status:
            row["http_status"] = int(status.group(1))
    else:
        row["error_detail"] = "redacted"


async def probe(spec, lang: str, *, timeout: float = 6.0) -> dict:
    started = time.monotonic()
    row = {"provider": spec.name, "language": lang, "status": "UNAVAILABLE", "count": 0}
    if any(not os.environ.get(key) for key in spec.requires):
        row["status"] = "AUTH_REQUIRED"
        row["elapsed_ms"] = 0
        return row
    adapter = None
    try:
        adapter = spec.adapter()
        effective_timeout = float(getattr(adapter, "smoke_timeout_seconds", timeout))
        result = await asyncio.wait_for(
            adapter.search(TERMS[lang], lang, SearchLimits(1, 3, effective_timeout)),
            timeout=effective_timeout,
        )
        row["status"] = str(adapter.support_status)
        row["count"] = len(result)
    except RateLimited as exc:
        row["status"] = "RATE_LIMITED"
        _diagnostic(row, exc)
    except AuthRequired as exc:
        row["status"] = "SESSION_REQUIRED" if str(exc) == "session_required" else "AUTH_REQUIRED"
        _diagnostic(row, exc)
    except (ProviderTimeout, asyncio.TimeoutError) as exc:
        row["status"] = "TIMEOUT"
        _diagnostic(row, exc)
    except Exception as exc:
        row["status"] = "UNAVAILABLE"
        _diagnostic(row, exc)
    finally:
        client = getattr(adapter, "client", None) if adapter is not None else None
        if client is not None and hasattr(client, "aclose"):
            await client.aclose()
    row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return row


def github_usage() -> dict:
    """Metrics actually observed by this run, not invented billing values."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    values = {
        "run_id": run_id or None,
        "release_bytes_first_page": None,
        "release_assets_first_page": None,
        "release_bytes_scope": "up to 100 newest releases, API page 1",
        "actions_billed_minutes": None,
        "actions_cost_usd": None,
        "billing_note": "Actions billing and cost cannot be inferred from job wall time",
    }
    if repo != REPO or not token:
        return values
    headers = {
        "authorization": "Bearer " + token,
        "accept": "application/vnd.github+json",
        "user-agent": "clipoteca-smoke",
    }
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            resp = client.get(
                "https://api.github.com/repos/" + repo + "/releases?per_page=100", headers=headers
            )
            resp.raise_for_status()
            releases = resp.json()
            values["release_bytes_first_page"] = sum(
                a.get("size", 0)
                for release in releases
                if isinstance(release, dict)
                for a in release.get("assets", [])
                if isinstance(a, dict) and isinstance(a.get("size"), int)
            )
            values["release_assets_first_page"] = sum(
                len(release.get("assets", [])) for release in releases if isinstance(release, dict)
            )
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return values


async def main_async() -> dict:
    started = time.monotonic()
    sem = asyncio.Semaphore(6)

    async def bounded_provider(spec):
        async with sem:
            rows = []
            languages = [lang for lang in TERMS if lang in spec.adapter.accepts_text_languages]
            for index, language in enumerate(languages):
                # No parallel requests to the same provider; avoid generating
                # the 429s that the smoke is intended to diagnose.
                if index:
                    await asyncio.sleep(0.5)
                rows.append(await probe(spec, language))
            return rows

    batches = await asyncio.gather(*(bounded_provider(spec) for spec in SOURCES))
    results = [row for batch in batches for row in batch]
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "github": {
            "ref": os.getenv("GITHUB_REF") or None,
            "sha": os.getenv("GITHUB_SHA") or None,
            "run_id": os.getenv("GITHUB_RUN_ID") or None,
        },
        "mode": "live_network_probes",
        "provider_languages": results,
        "total_probes": len(results),
        "elapsed_seconds_measured": round(time.monotonic() - started, 3),
        "github_usage": github_usage(),
        "notes": [
            "Real API search probes; successful search does not imply download rights or publication.",
            "No release upload, FFmpeg or deployed Worker E2E is asserted by this smoke.",
            "Unconfigured credentials are AUTH_REQUIRED, not supplier failures.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/reports/smoke.json")
    # The existing workflow invokes this script without --mode.
    parser.add_argument("--mode", choices=("all", "providers", "candidates"), default="all")
    args = parser.parse_args(argv)

    async def collect() -> dict:
        if args.mode == "candidates":
            return await candidates_smoke()
        provider_report = await main_async()
        if args.mode == "all":
            candidate_report = await candidates_smoke()
            # Preserve the original provider metrics and embed the sanitized
            # candidate report in the same data/reports/ JSON artifact.
            provider_report["candidates"] = candidate_report
            provider_report["mode"] = "live_network_and_candidate_probes"
            provider_report["total_candidate_probes"] = candidate_report["total_probes"]
        return provider_report

    data = asyncio.run(collect())
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "probes": data["total_probes"] + data.get("total_candidate_probes", 0),
                "elapsed_seconds_measured": data["elapsed_seconds_measured"],
                "report": str(path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
