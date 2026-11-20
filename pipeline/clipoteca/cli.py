"""CLI for offline preview and the Actions-only ingestion workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .ingest import ingest
from .jobs import request_id as calculate_request_id
from .providers.internet_archive import InternetArchive
from .providers.nasa import NASA
from .providers.pexels import Pexels
from .providers.pixabay import Pixabay
from .providers.wikimedia_commons import WikimediaCommons
from .translate.expand import expand_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipoteca")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--aspect-ratio")
    search.add_argument("--max-duration", type=float, default=20)
    search.add_argument("--max-results", type=int, default=25)
    search.add_argument("--dry-run", action="store_true")

    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("--query", required=True)
    ingest_parser.add_argument("--request-id", default="")
    ingest_parser.add_argument("--max-results", type=int, default=3)
    ingest_parser.add_argument("--aspect-ratio")
    ingest_parser.add_argument("--max-duration", type=float, default=20)
    ingest_parser.add_argument("--horizon", default="v1")
    ingest_parser.add_argument("--concurrency", type=int, default=4)
    ingest_parser.add_argument("--job-root", default="data/jobs")

    args = parser.parse_args(argv)
    if args.command == "version":
        print("clipoteca 0.1.0")
        return 0
    if args.command == "search":
        expansion = expand_query(args.query)
        print(
            json.dumps(
                {
                    "concept": expansion.concept,
                    "terms": expansion.terms,
                    "expanded": expansion.expanded,
                    "errors": expansion.errors,
                    "dry_run": args.dry_run,
                    "filters": {
                        "aspect_ratio": args.aspect_ratio,
                        "max_duration": args.max_duration,
                        "max_results": args.max_results,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if expansion.complete else 2
    if args.command == "ingest":
        repository = os.getenv("GITHUB_REPOSITORY", "")
        token = os.getenv("GITHUB_TOKEN", "")
        if not token or not repository:
            print("GITHUB_TOKEN and GITHUB_REPOSITORY required", file=sys.stderr)
            return 2
        expected = calculate_request_id(
            args.query,
            {"aspect_ratio": args.aspect_ratio, "max_duration": args.max_duration},
            args.horizon,
        )
        if args.request_id and args.request_id != expected:
            print("request_id mismatch", file=sys.stderr)
            return 2

        async def run():
            adapters = [Pexels(), Pixabay(), WikimediaCommons(), InternetArchive(), NASA()]
            try:
                return await ingest(
                    args.query,
                    adapters,
                    repository=repository,
                    token=token,
                    max_results=args.max_results,
                    concurrency=args.concurrency,
                    job_root=Path(args.job_root),
                    filters={"aspect_ratio": args.aspect_ratio, "max_duration": args.max_duration},
                    horizon=args.horizon,
                )
            finally:
                for adapter in adapters:
                    await adapter.client.aclose()

        report = asyncio.run(run())
        print(
            json.dumps(
                {
                    "request_id": report.request_id,
                    "status": report.status,
                    "published_ids": report.published_ids,
                    "error_types": tuple(error.rsplit(":", 1)[-1] for error in report.errors),
                },
                ensure_ascii=False,
            )
        )
        return 0 if report.status in {"published", "duplicate", "no_results"} else 1
    return 1
