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
from .providers.bilibili import Bilibili
from .providers.douyin import Douyin
from .providers.tiktok import TikTok
from .providers.vk_video import VKVideo
from .providers.nasa import NASA
from .providers.pexels import Pexels
from .providers.instagram import Instagram
from .providers.snapchat import Snapchat
from .providers.peertube import PeerTube
from .providers.odysee import Odysee
from .providers.rutube import Rutube
from .providers.pixabay import Pixabay
from .providers.wikimedia_commons import WikimediaCommons
from .providers.youtube import YouTube
from .translate.expand import expand_query
from .sources import list_sources
from .relocalize import relocalize_catalog
from .metadata import MetadataTranslationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipoteca")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("list_sources", aliases=["list-sources"])
    export = commands.add_parser("export_sources", aliases=["export-sources"])
    export.add_argument("--output", default="data/sources.json")
    relocalize = commands.add_parser("relocalize")
    relocalize.add_argument("--ids", nargs="+", required=True)
    relocalize.add_argument("--language-overrides", nargs="*", default=[])
    relocalize.add_argument("--catalog", default="data/clips.json")
    relocalize.add_argument("--index-root", default="data/index")
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
    ingest_parser.add_argument("--providers", default="", help="Comma-separated source names")
    ingest_parser.add_argument("--job-root", default="data/jobs")
    ingest_parser.add_argument("--filters-json", default="")

    args = parser.parse_args(argv)
    if args.command in ("list_sources", "list-sources"):
        sources = list_sources()
        print(json.dumps({"count": len(sources), "sources": sources}, ensure_ascii=False, indent=2))
        return 0
    if args.command in ("export_sources", "export-sources"):
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(list_sources(env={}), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(target)
        return 0
    if args.command == "relocalize":
        try:
            identifiers = [part for value in args.ids for part in value.replace(",", " ").split()]
            overrides = {}
            for value in args.language_overrides:
                for pair in value.replace(",", " ").split():
                    identifier, language = pair.split(":", 1)
                    overrides[identifier] = language
            updated = relocalize_catalog(
                Path(args.catalog),
                identifiers,
                index_root=Path(args.index_root),
                language_overrides=overrides,
            )
        except (ValueError, MetadataTranslationError) as exc:
            print(f"relocalize_failed:{exc}", file=sys.stderr)
            return 2
        print(json.dumps({"relocalized_ids": updated}, ensure_ascii=False))
        return 0
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
        selected = {name.strip() for name in args.providers.split(",") if name.strip()}
        source_names = {source["name"] for source in list_sources(env={})}
        if selected - source_names:
            print("unknown source in --providers", file=sys.stderr)
            return 2
        if args.filters_json:
            try:
                filters = json.loads(args.filters_json)
                if not isinstance(filters, dict) or any(
                    key
                    not in {
                        "aspect_ratio",
                        "max_duration",
                        "min_width",
                        "fps",
                        "source",
                        "language",
                        "has_audio",
                        "limit",
                        "cursor",
                    }
                    for key in filters
                ):
                    raise ValueError("invalid filters")
            except (ValueError, TypeError):
                print("invalid canonical filters", file=sys.stderr)
                return 2
            source = filters.get("source")
            if source and source not in source_names:
                print("unknown source in filters", file=sys.stderr)
                return 2
            if source:
                selected = {source}
        else:
            filters = {"aspect_ratio": args.aspect_ratio, "max_duration": args.max_duration}
            if selected:
                filters["providers"] = sorted(selected)
        expected = calculate_request_id(args.query, filters, args.horizon)
        if args.request_id and args.request_id != expected:
            print("request_id mismatch", file=sys.stderr)
            return 2

        async def run():
            adapters = [
                Pexels(),
                Pixabay(),
                WikimediaCommons(),
                InternetArchive(),
                NASA(),
                YouTube(),
                Rutube(),
                PeerTube(),
                Odysee(),
                Bilibili(),
                VKVideo(),
                Douyin(),
                TikTok(),
                Instagram(),
                Snapchat(),
            ]
            try:
                allowed = [
                    adapter for adapter in adapters if not selected or adapter.name in selected
                ]
                return await ingest(
                    args.query,
                    allowed,
                    repository=repository,
                    token=token,
                    max_results=min(int(filters.get("limit") or args.max_results), 50),
                    concurrency=args.concurrency,
                    job_root=Path(args.job_root),
                    filters=filters,
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
