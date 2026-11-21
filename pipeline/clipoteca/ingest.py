"""Actions-only asynchronous ingestion: source → verified Releases → catalogue."""

from __future__ import annotations

import asyncio
import os
import re
import time
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from .catalog import DuplicateClip, GitHubCatalogWriter, IdentityCollision
from .engine import SearchEngine
from .jobs import (
    MAX_ATTEMPTS,
    RETRYABLE,
    STATUSES,
    GitHubJobWriter,
    JobStore,
    expired_dead_letter,
    new_job,
    request_id,
)
from .media.hashing import perceptual_hash, perceptually_similar, sha256_file
from .media.normalize import normalize_media
from .media.thumbnail import create_thumbnail
from .metadata import MetadataTranslationError, localize_candidate
from .metrics import discovery_metrics, write_metrics
from .providers.base import Adapter, AuthRequired, RateLimited, SupportStatus
from .translate.expand import QueryExpansion, expand_query
from .security.download import download_media
from .storage.github import GitHubReleaseStorage, StorageError


@dataclass(frozen=True)
class IngestReport:
    request_id: str
    status: str
    published_ids: tuple[str, ...]
    errors: tuple[str, ...]


async def ingest(
    query: str,
    adapters: Sequence[Adapter],
    *,
    repository: str,
    token: str,
    job_root: Path = Path("data/jobs"),
    max_results: int = 3,
    concurrency: int = 4,
    filters: dict | None = None,
    horizon: str = "v1",
    writer: GitHubCatalogWriter | None = None,
    storage: GitHubReleaseStorage | None = None,
    downloader: Callable[[str, Path], Awaitable[Path]] | None = None,
    translator=None,
    expand_search: bool = True,
) -> IngestReport:
    if not expand_search and (len(adapters) != 1 or adapters[0].name != "community"):
        raise ValueError("partial expansion is allowed only for a direct community URL")
    if concurrency < 1 or concurrency > 16:
        raise ValueError("concurrency must be 1..16")
    if max_results < 1 or max_results > 50:
        raise ValueError("max_results must be 1..50")
    if float((filters or {}).get("max_duration") or 20) > 20:
        raise ValueError("max_duration must be at most 20 seconds")
    rid = request_id(query, filters or {}, horizon)
    remote_jobs = (
        GitHubJobWriter(repository, token)
        if os.environ.get("CLIPOTECA_PERSIST_JOBS") == "1"
        else None
    )
    jobs = JobStore(job_root, remote=remote_jobs)
    existing = jobs.load(rid)
    if existing and existing["status"] in {"published", "duplicate"}:
        return IngestReport(rid, "duplicate", tuple(existing.get("duplicate_ids", ())), ())
    if existing and existing["status"] == "dead_letter":
        if not expired_dead_letter(existing):
            return IngestReport(rid, "dead_letter", (), ("retry_budget_exhausted",))
        existing = None  # after 24 h, a new bounded retry window is permitted
    if existing and existing["status"] == "no_results":
        return IngestReport(rid, "no_results", (), ())
    if existing and existing["status"] in RETRYABLE:
        if existing.get("attempt", 1) >= MAX_ATTEMPTS:
            blocked = dict(existing)
            blocked["status"] = "dead_letter"
            jobs.save(blocked)
            return IngestReport(rid, "dead_letter", (), ("retry_budget_exhausted",))
        job = new_job(rid)
        job["attempt"] = existing.get("attempt", 1) + 1
    else:
        job = existing or new_job(rid)
    if existing is None or job is not existing:
        jobs.save(job)  # Durable queued state before each new attempt.
    writer = writer or GitHubCatalogWriter(repository, token)
    own_storage = storage is None
    storage = storage or GitHubReleaseStorage(repository, token=token)
    errors: list[str] = []
    ids: list[str] = []
    downloaded_count = 0
    failed_count = 0
    rate_limited_count = 0
    unavailable_count = 0
    storage_bytes = 0
    published_seconds = 0.0
    ingest_started = time.monotonic()

    def advance(stage: str) -> None:
        if STATUSES.index(stage) > STATUSES.index(job["status"]):
            job["status"] = stage
        jobs.save(job)

    try:
        advance("searching")
        expansion = (
            expand_query(query, translator=translator)
            if expand_search
            else QueryExpansion(concept=query, terms={"es": query})
        )
        job["terms"] = dict(expansion.terms)
        if expand_search and (not expansion.complete or expansion.errors):
            missing = next(
                (
                    language
                    for language in ("es", "en", "zh", "ru")
                    if not expansion.terms.get(language)
                ),
                next(iter(expansion.errors), "unknown"),
            )
            job["status"] = "dead_letter" if job.get("attempt", 1) >= MAX_ATTEMPTS else "failed"
            job["source_errors"]["translation"] = f"translation_failed:{missing}"
            jobs.save(job)
            return IngestReport(rid, "failed", (), (f"translation_failed:{missing}",))
        catalogue, _ = writer.read_catalog()
        search = await SearchEngine(adapters, concurrency=concurrency).search(
            query, catalog=catalogue, expansion=expansion,
            allow_partial_expansion=not expand_search,
        )
        for execution in search.executions:
            job["language_counts"][execution.language] = (
                job["language_counts"].get(execution.language, 0) + 1
            )
            job["provider_counts"][execution.provider] = (
                job["provider_counts"].get(execution.provider, 0) + execution.found
            )
            if execution.error:
                job["source_errors"][f"{execution.provider}:{execution.language}"] = execution.error
        write_metrics(discovery_metrics(search), job_root / (rid + ".metrics.json"))
        if not search.candidates:
            statuses = [execution.status for execution in search.executions]
            if search.duplicates:
                final = "duplicate"
                job["duplicate_ids"] = search.duplicate_ids
            elif statuses and all(item == SupportStatus.RATE_LIMITED for item in statuses):
                final = "rate_limited"
            elif statuses and all(
                item
                in {
                    SupportStatus.UNAVAILABLE,
                    SupportStatus.AUTH_REQUIRED,
                    SupportStatus.TIMEOUT,
                    SupportStatus.RATE_LIMITED,
                }
                for item in statuses
            ):
                final = "unavailable"
            else:
                final = "no_results"
            job["status"] = (
                "dead_letter"
                if final in RETRYABLE and job.get("attempt", 1) >= MAX_ATTEMPTS
                else final
            )
            jobs.save(job)
            return IngestReport(
                rid,
                job["status"],
                tuple(job.get("duplicate_ids", ())) if final == "duplicate" else (),
                (),
            )
        by_name = {adapter.name: adapter for adapter in adapters}
        prioritized = sorted(
            search.candidates,
            key=lambda candidate: (
                by_name[candidate.provider].support_status
                != SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
            ),
        )
        for candidate in prioritized:
            if len(ids) >= max_results:
                break
            adapter = by_name[candidate.provider]
            requested_language = (filters or {}).get("language")
            if requested_language and candidate.source_language != requested_language:
                errors.append(f"{candidate.origin_id}:language_filter")
                continue
            if adapter.support_status != SupportStatus.SUPPORTED_SEARCH_DOWNLOAD:
                errors.append(f"{candidate.origin_id}:search_only")
                job["source_errors"][candidate.origin_id] = "search_only"
                continue
            with tempfile.TemporaryDirectory(prefix="clipoteca-ingest-") as work:
                temp = Path(work)
                try:
                    advance("fetching")
                    ref = await adapter.direct_media(candidate)
                    if ref is None:
                        errors.append(f"{candidate.origin_id}:search_only")
                        job["source_errors"][candidate.origin_id] = "search_only"
                        continue
                    # Fail multilingual metadata before spending bandwidth on a large video.
                    localized = localize_candidate(
                        candidate, translator=translator,
                        include_query_tags=candidate.provider != "community",
                    )
                    raw = await (
                        downloader(ref.url, temp)
                        if downloader
                        else download_media(ref.url, temp, expected_bytes=ref.size_bytes)
                    )
                    downloaded_count += 1
                    advance("processing")
                    duration_limit = float((filters or {}).get("max_duration") or 20)
                    normalized = normalize_media(
                        raw, temp / "normalized.mp4", max_duration_seconds=duration_limit
                    )
                    desired_aspect = (filters or {}).get("aspect_ratio")
                    if desired_aspect and normalized.media.aspect_ratio != desired_aspect:
                        errors.append(f"{candidate.origin_id}:aspect_filter")
                        continue
                    desired_width = (filters or {}).get("min_width")
                    desired_fps = (filters or {}).get("fps")
                    desired_audio = (filters or {}).get("has_audio")
                    if (
                        (desired_width and normalized.media.width < int(desired_width))
                        or (desired_fps and normalized.media.fps < float(desired_fps))
                        or (
                            desired_audio is not None
                            and normalized.media.has_audio != desired_audio
                        )
                    ):
                        errors.append(f"{candidate.origin_id}:media_filter")
                        continue
                    thumb_path = create_thumbnail(normalized.path, temp / "thumbnail.webp")
                    digest = sha256_file(normalized.path)
                    frame_hash = perceptual_hash(normalized.path)
                    # Optimistic read before expensive release upload; writer rechecks on PUT.
                    remote, _ = writer.read_catalog()
                    draft = {
                        "source": {
                            "provider": candidate.provider,
                            "provider_id": candidate.provider_id,
                        },
                        "media": {"sha256": digest, "perceptual_hash": frame_hash},
                    }
                    duplicate = remote.find_duplicate(draft, phash_similar=perceptually_similar)
                    if duplicate:
                        errors.append(f"{candidate.origin_id}:duplicate:{duplicate['id']}")
                        job.setdefault("duplicate_ids", []).append(duplicate["id"])
                        continue
                    release = storage.get_or_create_batch_release(date.today())
                    reserved = (
                        storage.reserved_ids(release) if hasattr(storage, "reserved_ids") else set()
                    )
                    for _attempt in range(4):
                        identifier = remote.next_id(reserved_ids=reserved)
                        clip = {
                            "id": identifier,
                            "title": localized.title,
                            "description": localized.description,
                            "tags": localized.tags,
                            "source": {
                                "provider": candidate.provider,
                                "provider_id": candidate.provider_id,
                                "source_language": localized.original_language,
                                "source_title": localized.original_title,
                                "source_url": candidate.source_url,
                                **(
                                    {
                                        key: candidate.metadata[key]
                                        for key in ("issue_number", "issue_author", "issue_url")
                                        if candidate.metadata and key in candidate.metadata
                                    }
                                    if candidate.provider == "community"
                                    else {}
                                ),
                            },
                            "media": {
                                "duration_seconds": normalized.media.duration_seconds,
                                "width": normalized.media.width,
                                "height": normalized.media.height,
                                "fps": normalized.media.fps,
                                "aspect_ratio": normalized.media.aspect_ratio,
                                "orientation": normalized.media.orientation,
                                "codec": normalized.media.codec,
                                "container": normalized.media.container,
                                "has_audio": normalized.media.has_audio,
                                "size_bytes": normalized.path.stat().st_size,
                                "sha256": digest,
                                "perceptual_hash": frame_hash,
                            },
                            "storage": {
                                "provider": "github_releases",
                                "asset_url": "",
                                "thumbnail_url": "",
                            },
                            "status": "published",
                            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        }
                        advance("publishing")
                        name_video, name_thumb = (
                            f"clip_{identifier}.mp4",
                            f"thumb_{identifier}.webp",
                        )
                        publish_started = time.monotonic()
                        try:
                            video = storage.upload_asset(release, normalized.path, name_video)
                            video = storage.verify_asset(
                                name_video, normalized.path.stat().st_size, digest, release.id
                            )
                            thumbnail_digest = sha256_file(thumb_path)
                            thumbnail = storage.upload_asset(release, thumb_path, name_thumb)
                            thumbnail = storage.verify_asset(
                                name_thumb, thumb_path.stat().st_size, thumbnail_digest, release.id
                            )
                        except StorageError:
                            # A partial upload reserves this ID. Never overwrite or delete
                            # another attempt's asset; a retry gets a distinct clip ID.
                            reserved.add(identifier)
                            remote, _ = writer.read_catalog()
                            if remote.find_duplicate(draft, phash_similar=perceptually_similar):
                                errors.append(f"{candidate.origin_id}:duplicate")
                                break
                            continue
                        clip["storage"]["asset_url"] = video.browser_download_url
                        clip["storage"]["thumbnail_url"] = thumbnail.browser_download_url
                        try:
                            writer.update(
                                [clip],
                                verified_assets={identifier: (video, thumbnail)},
                                phash_similar=perceptually_similar,
                            )
                            ids.append(identifier)
                            storage_bytes += video.size + thumbnail.size
                            published_seconds += time.monotonic() - publish_started
                            catalogue.merge([clip])
                            break
                        except IdentityCollision:
                            reserved.add(identifier)
                            remote, _ = writer.read_catalog()
                            continue
                        except DuplicateClip as duplicate:
                            errors.append(f"{candidate.origin_id}:duplicate:{duplicate.clip_id}")
                            job.setdefault("duplicate_ids", []).append(duplicate.clip_id)
                            break
                    else:
                        errors.append(f"{candidate.origin_id}:identity_collision")
                except RateLimited:
                    failed_count += 1
                    rate_limited_count += 1
                    errors.append(f"{candidate.origin_id}:rate_limited")
                except AuthRequired:
                    failed_count += 1
                    unavailable_count += 1
                    errors.append(f"{candidate.origin_id}:auth_required")
                except Exception as exc:
                    failed_count += 1
                    # Never print secrets, URLs or raw upstream responses to Actions logs.
                    error_code = type(exc).__name__
                    if isinstance(exc, MetadataTranslationError):
                        direction = re.search(
                            r"translation_failed:(?:es|en|zh|ru)->(es|en|zh|ru)",
                            str(exc),
                        )
                        if direction:
                            error_code = f"translation_failed:{direction.group(1)}"
                    job["source_errors"][candidate.origin_id] = error_code
                    errors.append(f"{candidate.origin_id}:{error_code}")
        if ids:
            job["status"] = "published"
        elif errors and all(":duplicate" in entry for entry in errors):
            job["status"] = "duplicate"
        elif errors and all(
            ":search_only" in entry or ":aspect_filter" in entry for entry in errors
        ):
            job["status"] = "unavailable"
        elif errors and rate_limited_count and rate_limited_count == len(errors):
            job["status"] = "rate_limited"
        elif errors and unavailable_count and unavailable_count == len(errors):
            job["status"] = "unavailable"
        else:
            job["status"] = "failed" if errors else "no_results"
        if job["status"] in RETRYABLE and job.get("attempt", 1) >= MAX_ATTEMPTS:
            job["status"] = "dead_letter"
        jobs.save(job)
        summary = discovery_metrics(search)
        summary.update(
            {
                "downloaded_clips": downloaded_count,
                "published_clips": len(ids),
                "failed_imports": failed_count,
                "assets_storage_bytes": storage_bytes,
                "mean_publish_duration_seconds": published_seconds / max(1, len(ids)),
                "runner_elapsed_minutes_estimate": (time.monotonic() - ingest_started) / 60,
            }
        )
        write_metrics(summary, job_root / (rid + ".metrics.json"))
        return IngestReport(
            rid, job["status"], tuple(ids or job.get("duplicate_ids", ())), tuple(errors)
        )
    except asyncio.CancelledError:
        job["status"] = "failed"
        job["source_errors"]["pipeline"] = "cancelled"
        jobs.save(job)
        raise
    except Exception as exc:
        # The pipeline must not leave searching/queued records indefinitely.
        # Only error class names are persisted, never upstream payloads or URLs.
        if job.get("status") not in {"published", "duplicate"}:
            job["status"] = "failed"
            job["source_errors"]["pipeline"] = type(exc).__name__
            jobs.save(job)
        return IngestReport(rid, "failed", tuple(ids), (type(exc).__name__,))
    finally:
        if own_storage:
            storage.close()
