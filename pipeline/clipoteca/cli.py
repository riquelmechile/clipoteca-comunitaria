"""CLI for deterministic preview and later ingestion."""

from __future__ import annotations

import argparse
import json

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
    return 1
