"""Ephemeral branch-only, read-only discovery benchmark for v2.0.6."""

import asyncio
import json
import os
from collections import Counter

from clipoteca.cli import _MissingCredentialsAdapter, _UnavailableAdapter
from clipoteca.engine import SearchEngine
from clipoteca.providers.base import SupportStatus
from clipoteca.sources import SOURCES

QUERY = "bosque"


async def main():
    adapters = []
    eligible = 0
    for spec in SOURCES:
        if any(not os.environ.get(key) for key in spec.requires):
            adapters.append(
                _MissingCredentialsAdapter(
                    spec.name, spec.adapter.accepts_text_languages
                )
            )
            continue
        try:
            adapter = spec.adapter()
        except Exception as exc:
            adapter = _UnavailableAdapter(
                spec.name, spec.adapter.accepts_text_languages, type(exc).__name__
            )
        else:
            if adapter.support_status != SupportStatus.UNAVAILABLE:
                eligible += 1
        adapters.append(adapter)
    try:
        result = await SearchEngine(adapters, concurrency=16).search(QUERY)
        report = {}
        for lang in ("es", "en", "zh", "ru"):
            counts = Counter()
            for entry in result.executions:
                if entry.language != lang:
                    continue
                if entry.error in ("discovery_deadline", "discovery_hard_cap"):
                    counts["discovery_deadline"] += 1
                elif entry.error == "provider_timeout":
                    counts["timeout"] += 1
                elif entry.status in (
                    SupportStatus.SUPPORTED_SEARCH_DOWNLOAD,
                    SupportStatus.SUPPORTED_SEARCH_ONLY,
                ):
                    counts["ok"] += 1
                elif entry.status == SupportStatus.AUTH_REQUIRED:
                    counts["auth_required"] += 1
                elif entry.status == SupportStatus.RATE_LIMITED:
                    counts["rate_limited"] += 1
                else:
                    counts["other"] += 1
            report[lang] = {
                key: counts[key]
                for key in (
                    "ok", "timeout", "discovery_deadline", "auth_required",
                    "rate_limited", "other",
                )
            }
        print(
            "V206_DISCOVERY="
            + json.dumps(
                {
                    "query": QUERY,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "credentialed_providers": eligible,
                    "counts": report,
                    "sources_with_candidates": sorted(
                        {candidate.provider for candidate in result.candidates}
                    ),
                    "candidates": len(result.candidates),
                },
                sort_keys=True,
            )
        )
        assert sum(c["discovery_deadline"] for c in report.values()) == 0
        assert result.elapsed_seconds < 180
    finally:
        for adapter in adapters:
            client = getattr(adapter, "client", None)
            if client is not None:
                await client.aclose()


asyncio.run(main())
