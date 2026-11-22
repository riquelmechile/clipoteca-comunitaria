"""CLI for offline preview and the Actions-only ingestion workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from . import __version__
from .ingest import ingest
from .jobs import (
    GitHubJobWriter,
    JobStore,
    new_job,
    request_id as calculate_request_id,
)
from .translate.expand import expand_query
from .sources import SOURCES, list_sources
from .providers.base import Adapter, SupportStatus
from .relocalize import relocalize_catalog
from .metadata import MetadataTranslationError


class _MissingCredentialsAdapter(Adapter):
    """Unavailable credentials cannot consume the discovery semaphore."""

    support_status = SupportStatus.AUTH_REQUIRED

    def __init__(self, name: str, languages: tuple[str, ...]) -> None:
        self.name = name
        self.accepts_text_languages = languages

    async def search(self, term, lang, limits):
        return []

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


class _UnavailableAdapter(Adapter):
    """Safe substitute when a single provider cannot be initialized."""

    support_status = SupportStatus.UNAVAILABLE

    def __init__(self, name: str, languages: tuple[str, ...], error_type: str) -> None:
        self.name = name
        self.accepts_text_languages = languages
        # Class name only: never persist raw exception values/credentials.
        self.unavailable_reason = "adapter_init_" + error_type

    async def search(self, term, lang, limits):
        return []

    async def metadata(self, candidate):
        return {}

    async def direct_media(self, candidate):
        return None


def _persist_request_id_mismatch(identifier: str, repository: str, token: str, root: Path) -> None:
    """Preserve a failed job receipt for an accepted but mismatched dispatch.

    Never overwrite a terminal successful job on a wrong identifier.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        return
    remote = (
        GitHubJobWriter(repository, token) if os.getenv("CLIPOTECA_PERSIST_JOBS") == "1" else None
    )
    jobs = JobStore(root, remote=remote)
    old = jobs.load(identifier)
    if old is not None and old["status"] in {"published", "duplicate", "no_results", "dead_letter"}:
        return
    record = dict(old) if old is not None else new_job(identifier)
    record["status"] = "failed"
    record["source_errors"] = {**record.get("source_errors", {}), "pipeline": "request_id_mismatch"}
    jobs.save(record)


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
    query_embedding = commands.add_parser("query-embedding")
    query_embedding.add_argument("--query", required=True)
    query_embedding.add_argument("--root", default="data/embeddings")
    migrate = commands.add_parser("migrate-embeddings")
    migrate.add_argument("--root", default="data/embeddings")
    migrate.add_argument("--publish", action="store_true")
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
    ingest_parser.add_argument(
        "--max-clips-per-source",
        type=int,
        default=2,
        help="Maximum disjoint windows per video (1..3)",
    )
    ingest_parser.add_argument("--aspect-ratio")
    ingest_parser.add_argument("--max-duration", type=float, default=20)
    ingest_parser.add_argument("--horizon", default="v1")
    ingest_parser.add_argument("--concurrency", type=int, default=16)
    ingest_parser.add_argument("--providers", default="", help="Comma-separated source names")
    ingest_parser.add_argument(
        "--runner",
        choices=("actions", "self-hosted"),
        default=os.getenv("CLIPOTECA_RUNNER", "actions"),
    )
    ingest_parser.add_argument("--runner-config", default="data/runner-routing.json")
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
        print("clipoteca " + __version__)
        return 0
    if args.command == "migrate-embeddings":
        from .media.embedding_store import EmbeddingStore
        from .media.shards import GitHubShards, migrate_legacy

        manifest = migrate_legacy(Path(args.root))
        if args.publish:
            repository = os.getenv("GITHUB_REPOSITORY", "")
            token = os.getenv("GITHUB_TOKEN", "")
            if not repository or not token:
                print("GITHUB_TOKEN and GITHUB_REPOSITORY required", file=sys.stderr)
                return 2
            import httpx

            with httpx.Client(
                timeout=30,
                headers={
                    "Authorization": "Bearer " + token,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ) as client:
                shards = GitHubShards(client, repository)
                legacy = EmbeddingStore(Path(args.root))
                rows = [
                    (identifier, legacy.read(identifier))
                    for identifier in sorted(legacy.index()["vectors"])
                ]
                shards.publish_many(rows)
        print(
            json.dumps(
                {
                    "version": manifest["version"],
                    "shards": len(manifest["shards"]),
                    "vectors": sum(row["count"] for row in manifest["shards"]),
                }
            )
        )
        return 0
    if args.command == "query-embedding":
        from .media.embeddings import load_encoder
        from .media.shards import GitHubQueries, QueryStore

        try:
            embedding = load_encoder().encode_text(args.query)
            path = QueryStore(Path(args.root)).put(args.query, embedding)
            repo = os.getenv("GITHUB_REPOSITORY", "")
            token = os.getenv("GITHUB_TOKEN", "")
            if repo and token:
                import httpx

                with httpx.Client(
                    timeout=30,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                ) as client:
                    GitHubQueries(client, repo).publish(args.query, embedding)
        except Exception:
            print("query embedding unavailable", file=sys.stderr)
            return 2
        print(json.dumps({"status": "ready", "path": str(path)}))
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
        from .runner import selected_providers

        try:
            routed = selected_providers(args.runner, selected, config_path=Path(args.runner_config))
        except (ValueError, OSError, json.JSONDecodeError):
            print("invalid runner routing configuration", file=sys.stderr)
            return 2
        requested = selected or source_names
        deferred = requested - routed
        deferred_reason = (
            "deferred_to_self_hosted" if args.runner == "actions" else "deferred_to_actions"
        )
        expected = calculate_request_id(args.query, filters, args.horizon)
        if args.request_id and args.request_id != expected:
            try:
                _persist_request_id_mismatch(
                    args.request_id, repository, token, Path(args.job_root)
                )
            except Exception:
                # Never echo an upstream exception that may contain credentials.
                print("request_id mismatch; job persistence unavailable", file=sys.stderr)
                return 2
            print("request_id mismatch", file=sys.stderr)
            return 2

        if not routed:
            try:
                remote = (
                    GitHubJobWriter(repository, token)
                    if os.getenv("CLIPOTECA_PERSIST_JOBS") == "1"
                    else None
                )
                jobs = JobStore(Path(args.job_root), remote=remote)
                old = jobs.load(expected)
                if old is None or old["status"] not in {"published", "duplicate", "no_results"}:
                    receipt = new_job(expected) if old is None else dict(old)
                    receipt["status"] = "unavailable"
                    receipt["source_errors"] = {
                        **receipt.get("source_errors", {}),
                        **{source: deferred_reason for source in sorted(deferred)},
                    }
                    jobs.save(receipt)
            except Exception:
                print("deferred runner job persistence unavailable", file=sys.stderr)
                return 2
            print(
                json.dumps(
                    {
                        "request_id": expected,
                        "status": "unavailable",
                        "runner": args.runner,
                        "error_types": [deferred_reason],
                        "deferred_sources": sorted(deferred),
                    }
                )
            )
            return 1
        selected = routed

        async def run():
            adapters = []
            for spec in SOURCES:
                if selected and spec.name not in selected:
                    continue
                if any(not os.environ.get(key) for key in spec.requires):
                    adapters.append(
                        _MissingCredentialsAdapter(spec.name, spec.adapter.accepts_text_languages)
                    )
                    continue
                try:
                    adapters.append(spec.adapter())
                except Exception as exc:
                    adapters.append(
                        _UnavailableAdapter(
                            spec.name, spec.adapter.accepts_text_languages, type(exc).__name__
                        )
                    )
            try:
                return await ingest(
                    args.query,
                    adapters,
                    repository=repository,
                    token=token,
                    max_results=min(int(filters.get("limit") or args.max_results), 50),
                    max_clips_per_source=args.max_clips_per_source,
                    concurrency=args.concurrency,
                    job_root=Path(args.job_root),
                    filters=filters,
                    horizon=args.horizon,
                    deferred_sources=tuple(sorted(deferred)),
                    deferred_reason=deferred_reason,
                )
            finally:
                for adapter in adapters:
                    client = getattr(adapter, "client", None)
                    if client is not None:
                        await client.aclose()

        report = asyncio.run(run())
        print(
            json.dumps(
                {
                    "request_id": report.request_id,
                    "status": report.status,
                    "published_ids": report.published_ids,
                    "error_types": tuple(error.rsplit(":", 1)[-1] for error in report.errors),
                    "deferred_sources": sorted(deferred),
                },
                ensure_ascii=False,
            )
        )
        return 0 if report.status in {"published", "duplicate", "no_results"} else 1
    return 1
