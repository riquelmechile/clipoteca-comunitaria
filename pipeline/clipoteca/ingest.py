"""Actions-only asynchronous ingestion: source \u2192 verified Releases \u2192 catalogue."""

from __future__ import annotations

import asyncio
import math
import os
import numpy as np
import re
import time
import tempfile
from functools import lru_cache

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from .catalog import (
    DuplicateClip,
    GitHubCatalogWriter,
    IdentityCollision,
    canonical_source_url,
    safe_source_url,
)
from .engine import SearchEngine, SearchResult
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
from .media.embeddings import (
    Encoder,
    EmbeddingUnavailable,
    FrameEmbeddings,
    clip_vector,
    cosine_scores,
    extract_frame_embeddings,
    load_encoder,
    window_similarity,
)
from .media.embedding_store import EmbeddingStore, GitHubEmbeddingWriter
from .media.shards import GitHubQueries, GitHubShards, QueryStore
from .media.hashing import perceptual_hash, perceptually_similar, sha256_file
from .media.normalize import NormalizeError, normalize_media
from .media.select import (
    SceneQualityRejected,
    SceneSelectionTimeout,
    SourceWindow,
    fallback_quality_window,
    rank_scene_windows,
)
from .media.storyboard import create_storyboard
from .media.probe import ProbeError, probe_media
from .media.thumbnail import create_thumbnail
from .metadata import MetadataTranslationError, localize_candidate
from .metrics import discovery_metrics, write_metrics
from .providers.base import Adapter, AuthRequired, RateLimited, SupportStatus
from .translate.argos import QUALITY_TRANSLATION_REASONS, public_translation_error
from .translate.expand import QueryExpansion, expand_query
from .security.download import DownloadError, download_dash, download_media
from .security.hls import HLS_MIME_TYPES, download_hls
from .storage.github import GitHubReleaseStorage, StorageError


@lru_cache(maxsize=4)
def _cached_embedding_encoder(loader: Callable) -> tuple[Encoder | None, str | None]:
    """Cache unavailable model initialization per loader within this process.

    Unlike load_encoder's lru_cache, an exception would otherwise be retried
    on every candidate source, wasting CPU and repeatedly loading model files.
    """
    try:
        return loader(), None
    except EmbeddingUnavailable:
        return None, "embeddings_unavailable:m14_quality_fallback"
    except Exception:
        return None, "embeddings_extraction_failed:m14_quality_fallback"


def _candidate_windows(
    raw: Path,
    *,
    duration_limit: float,
    max_clips: int,
    job: dict,
    origin_id: str,
    query: str = "",
    embedding_encoder: Encoder | None = None,
    embedding_frames: dict[str, FrameEmbeddings] | None = None,
) -> list[SourceWindow]:
    """Keep M14's hard quality gate; rank valid windows by 3s-smoothed cosine.

    Missing optional models or any bounded extraction failure falls back to the
    original deterministic M14 ranking and is recorded, never silently ignored.
    """
    scorer = None
    warnings = job.setdefault("source_warnings", {})
    if query:
        if embedding_encoder is None:
            encoder, error = _cached_embedding_encoder(load_encoder)
        else:
            encoder, error = embedding_encoder, None
        if error:
            warnings[origin_id] = error
        if encoder is not None:
            try:
                samples = extract_frame_embeddings(raw, encoder)
                similarities = cosine_scores(samples, encoder, query)

                def semantic_score(window: SourceWindow) -> float | None:
                    return window_similarity(
                        samples, similarities, start=window.start, end=window.end
                    )

                scorer = semantic_score
                if embedding_frames is not None:
                    embedding_frames[origin_id] = samples
                warnings[origin_id] = "embeddings_ranked"
            except Exception:
                warnings[origin_id] = "embeddings_extraction_failed:m14_quality_fallback"
    try:
        try:
            return rank_scene_windows(
                raw,
                max_duration=duration_limit,
                max_clips=max_clips,
                semantic_scorer=scorer,
            )
        except (SceneSelectionTimeout, SceneQualityRejected):
            raise
        except Exception:
            if scorer is None:
                raise
            # Scoring can fail within rank_scene_windows after extraction.
            # Retry with M14's original quality rank without allowing the
            # optional model to make ingest fail.
            warnings[origin_id] = "embeddings_extraction_failed:m14_quality_fallback"
            if embedding_frames is not None:
                embedding_frames.pop(origin_id, None)
            return rank_scene_windows(
                raw,
                max_duration=duration_limit,
                max_clips=max_clips,
                semantic_scorer=None,
            )
    except SceneSelectionTimeout:
        duration = probe_media(raw, timeout=30).duration_seconds
        try:
            window = fallback_quality_window(
                raw, max_duration=duration_limit, total_duration=duration
            )
        except SceneSelectionTimeout:
            timeout_key = (
                f"{origin_id}:scene_selection"
                if warnings.get(origin_id, "").startswith("embeddings_")
                else origin_id
            )
            warnings[timeout_key] = "scene_selection_timeout:fallback_timeout_rejected"
            raise SceneQualityRejected("fallback quality sampling timed out") from None
        timeout_key = (
            f"{origin_id}:scene_selection"
            if warnings.get(origin_id, "").startswith("embeddings_")
            else origin_id
        )
        warnings[timeout_key] = (
            f"scene_selection_timeout:quality_checked_fallback:{window.start:.3f}"
        )
        return [window]


def _embedding_result(job: dict) -> str:
    """Aggregate across ALL source origins; report the worst semantic outcome."""
    warnings = [
        value
        for value in job.get("source_warnings", {}).values()
        if isinstance(value, str) and value.startswith("embeddings_")
    ]
    for prefix in (
        "embeddings_persist_failed:",
        "embeddings_extraction_failed:",
        "embeddings_unavailable:",
        "embeddings_ranked",
    ):
        hit = next((item for item in warnings if item.startswith(prefix)), None)
        if hit:
            return hit
    return "not_attempted"


def _published_source_complete(
    clips: Sequence[dict],
    candidate_url: str,
    max_clips: int,
    *,
    candidate_duration: float | None = None,
    max_duration: float = 20,
) -> dict | None:
    """Skip a deterministic source once all requested explicit windows exist."""
    matching = [
        clip
        for clip in clips
        if clip.get("status") != "retired"
        and candidate_url
        and canonical_source_url(str(clip["source"].get("source_url", ""))) == candidate_url
    ]
    legacy = next((clip for clip in matching if "source_start" not in clip["source"]), None)
    if legacy is not None:
        return legacy
    ranged = [
        clip
        for clip in matching
        if "source_start" in clip["source"] and "source_end" in clip["source"]
    ]
    if len(ranged) >= max_clips:
        return ranged[0]
    if ranged and not candidate_duration:
        return ranged[0]
    if candidate_duration and candidate_duration > 0:
        if any(
            float(clip["source"]["source_start"]) <= 0.001
            and float(clip["source"]["source_end"]) + 0.001 >= candidate_duration
            for clip in ranged
        ):
            return ranged[0]
        minimum_duration = min(8.0, max_duration, candidate_duration)
        possible = (
            1
            if candidate_duration <= max_duration + minimum_duration
            else min(max_clips, math.ceil(candidate_duration / max_duration))
        )
        if len(ranged) >= possible:
            return ranged[0]
    return None


@dataclass(frozen=True)
class IngestReport:
    request_id: str
    status: str
    published_ids: tuple[str, ...]
    errors: tuple[str, ...]
    embedding_status: str = "not_attempted"


async def ingest(
    query: str,
    adapters: Sequence[Adapter],
    *,
    repository: str,
    token: str,
    job_root: Path = Path("data/jobs"),
    max_results: int = 3,
    max_clips_per_source: int = 2,
    concurrency: int = 16,
    filters: dict | None = None,
    horizon: str = "v1",
    writer: GitHubCatalogWriter | None = None,
    storage: GitHubReleaseStorage | None = None,
    downloader: Callable[[str, Path], Awaitable[Path]] | None = None,
    translator=None,
    expand_search: bool = True,
    embedding_encoder: Encoder | None = None,
    deferred_sources: tuple[str, ...] = (),
    deferred_reason: str = "deferred_to_self_hosted",
    discovery: SearchResult | None = None,
    scene_budget=None,
) -> IngestReport:
    if not expand_search and (len(adapters) != 1 or adapters[0].name != "community"):
        raise ValueError("partial expansion is allowed only for a direct community URL")
    if concurrency < 1 or concurrency > 16:
        raise ValueError("concurrency must be 1..16")
    if max_results < 1 or max_results > 50:
        raise ValueError("max_results must be 1..50")
    if not 1 <= max_clips_per_source <= 3:
        raise ValueError("max_clips_per_source must be 1..3")
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
        job["terminal_sources"] = list(existing.get("terminal_sources", ()))
    else:
        job = existing or new_job(rid)
    for source in deferred_sources:
        job.setdefault("source_errors", {})[source] = deferred_reason
    if existing is None or job is not existing or deferred_sources:
        jobs.save(job)  # Durable queued state and deferred sources.
    writer = writer or GitHubCatalogWriter(repository, token)
    own_storage = storage is None
    storage = storage or GitHubReleaseStorage(repository, token=token)
    errors: list[str] = [f"{source}:{deferred_reason}" for source in deferred_sources]
    ids: list[str] = []
    semantic_frames: dict[str, FrameEmbeddings] = {}
    embeddings_store = EmbeddingStore(job_root.parent / "embeddings")
    pending_remote_vectors: list[tuple[str, np.ndarray]] = []
    m21_sidecars = os.environ.get("CLIPOTECA_M21_SIDECARS", "1") != "0"
    remote_embeddings: GitHubEmbeddingWriter | None = None
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

    def record_duplicate(candidate, duplicate: dict, reason: str) -> None:
        clip_id = duplicate["id"]
        errors.append(f"{candidate.origin_id}:duplicate:{clip_id}:{reason}")
        duplicate_ids = job.setdefault("duplicate_ids", [])
        if clip_id not in duplicate_ids:
            duplicate_ids.append(clip_id)
        provenance = (
            "duplicate_cross_provider"
            if duplicate["source"]["provider"] != candidate.provider
            else "duplicate"
        )
        # The clip schema has a single source object. Preserve the discarded
        # provenance in the durable job instead of widening published records.
        job["source_errors"][candidate.origin_id] = f"{provenance}:{reason}:{clip_id}"

    try:
        advance("searching")
        # Save the same multilingual CLIP text vector used by M21 for M22.
        # The query can be cached even if discovery finds no clips.
        try:
            encoder, failure = (
                (embedding_encoder, None)
                if embedding_encoder is not None
                else _cached_embedding_encoder(load_encoder)
            )
            if encoder is not None:
                query_vector = encoder.encode_text(query)
                QueryStore(job_root.parent / "embeddings").put(query, query_vector)
                if os.environ.get("CLIPOTECA_PERSIST_JOBS") == "1":
                    if remote_embeddings is None:
                        remote_embeddings = GitHubEmbeddingWriter(repository, token)
                    GitHubQueries(remote_embeddings.client, repository).publish(query, query_vector)
            elif failure:
                job.setdefault("source_warnings", {})["semantic_query"] = failure
        except Exception:
            job.setdefault("source_warnings", {})["semantic_query"] = (
                "query_vector_cache_unavailable"
            )
        expansion = (
            discovery.expansion
            if discovery is not None
            else (
                expand_query(query, translator=translator)
                if expand_search
                else QueryExpansion(concept=query, terms={"es": query})
            )
        )
        job["terms"] = dict(expansion.terms)
        partial_quality_expansion = False
        if expand_search and (not expansion.complete or expansion.errors):
            fatal_errors = []
            for language in ("es", "en", "zh", "ru"):
                if expansion.terms.get(language) and language not in expansion.errors:
                    continue
                code = public_translation_error(expansion.errors.get(language, ""))
                if (
                    not expansion.terms.get(language)
                    and code
                    and code.rsplit(":", 1)[-1] in QUALITY_TRANSLATION_REASONS
                ):
                    job.setdefault("source_warnings", {})[f"translation:{language}"] = code
                    partial_quality_expansion = True
                else:
                    # Unknown, missing, and configuration failures remain fatal.
                    fatal_errors.append(code or f"translation_failed:{language}")
            if fatal_errors:
                job["status"] = "dead_letter" if job.get("attempt", 1) >= MAX_ATTEMPTS else "failed"
                job["source_errors"]["translation"] = fatal_errors[0]
                jobs.save(job)
                return IngestReport(rid, "failed", (), (fatal_errors[0],))
        catalogue, _ = writer.read_catalog()
        search = discovery
        if search is None:
            search = await SearchEngine(adapters, concurrency=concurrency).search(
                query,
                catalog=catalogue,
                expansion=expansion,
                allow_partial_expansion=not expand_search or partial_quality_expansion,
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
        # Give each provider a real first chance before any single video
        # consumes multiple publication slots. The fallback pass retains the
        # original multi-window cap when other sources fail quality/download.
        diverse_sources = (
            len(
                {
                    candidate.provider
                    for candidate in prioritized
                    if by_name[candidate.provider].support_status
                    == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
                }
            )
            > 1
        )
        selections = (
            [(candidate, 1) for candidate in prioritized]
            + [(candidate, max_clips_per_source) for candidate in prioritized]
            if diverse_sources and max_clips_per_source > 1
            else [(candidate, max_clips_per_source) for candidate in prioritized]
        )
        published_providers: set[str] = set()
        first_pass_size = len(prioritized) if diverse_sources and max_clips_per_source > 1 else 0
        for selection_index, (candidate, window_cap) in enumerate(selections):
            if scene_budget.reason() if scene_budget else len(ids) >= max_results:
                break
            # First pass: no provider takes a second slot while another
            # provider still has a candidate that might pass quality gates.
            if selection_index < first_pass_size and candidate.provider in published_providers:
                continue
            adapter = by_name[candidate.provider]
            requested_language = (filters or {}).get("language")
            if requested_language and candidate.source_language != requested_language:
                errors.append(f"{candidate.origin_id}:language_filter")
                continue
            if adapter.support_status != SupportStatus.SUPPORTED_SEARCH_DOWNLOAD:
                errors.append(f"{candidate.origin_id}:search_only")
                job["source_errors"][candidate.origin_id] = "search_only"
                continue
            # Avoid downloading deterministic sources whose requested scene ranges
            # are already represented. One legacy whole-source clip is also complete.
            candidate_url = canonical_source_url(candidate.source_url)
            if catalogue.is_retired_source(candidate_url):
                errors.append(f"{candidate.origin_id}:retired_source")
                job["source_errors"][candidate.origin_id] = "retired_source"
                continue
            if candidate_url in job.get("terminal_sources", ()):
                errors.append(f"{candidate.origin_id}:scene_quality_rejected")
                job["source_errors"][candidate.origin_id] = "scene_quality_rejected"
                continue
            duration_limit = float((filters or {}).get("max_duration") or 20)
            early_match = _published_source_complete(
                catalogue.clips,
                candidate_url,
                max_clips_per_source,
                candidate_duration=candidate.duration_seconds,
                max_duration=duration_limit,
            )
            if early_match is not None:
                record_duplicate(candidate, early_match, "canonical_url")
                continue
            with tempfile.TemporaryDirectory(prefix="clipoteca-ingest-") as work:
                temp = Path(work)
                try:
                    # Studio text gate uses source metadata, never the search terms.
                    # Do it before resolving media or starting any download.
                    if scene_budget:
                        localized = localize_candidate(candidate, translator=translator)
                        if not scene_budget.candidate_text_good(candidate, localized):
                            continue
                    advance("fetching")
                    ref = await adapter.direct_media(candidate)
                    if ref is None:
                        errors.append(f"{candidate.origin_id}:search_only")
                        job["source_errors"][candidate.origin_id] = "search_only"
                        continue

                    # Search terms are not evidence of the video's contents.
                    if not scene_budget:
                        localized = localize_candidate(candidate, translator=translator)
                    if scene_budget:
                        if scene_budget.reason():
                            break
                        scene_budget.downloads_started += 1
                        scene_budget.attempted.add(candidate.origin_id)
                    if downloader:
                        raw = await downloader(ref.url, temp)
                    elif ref.audio_url:
                        raw = await download_dash(
                            ref.url,
                            ref.audio_url,
                            temp,
                            allowed_hosts=ref.allowed_hosts,
                            referer=ref.referer or "https://www.bilibili.com/",
                        )
                    elif ref.mime in HLS_MIME_TYPES:
                        raw = await download_hls(
                            ref.url,
                            temp,
                            referer=ref.referer,
                            allowed_hosts=ref.allowed_hosts,
                            max_duration=duration_limit,
                            user_agent=ref.user_agent,
                        )
                    else:
                        raw = await download_media(
                            ref.url,
                            temp,
                            expected_bytes=ref.size_bytes,
                            allowed_hosts=ref.allowed_hosts,
                            referer=ref.referer if candidate.provider == "bilibili" else None,
                            user_agent=ref.user_agent,
                        )
                    downloaded_count += 1
                    if scene_budget:
                        scene_budget.downloads_completed += 1
                    advance("processing")
                    windows = _candidate_windows(
                        raw,
                        duration_limit=duration_limit,
                        max_clips=window_cap,
                        job=job,
                        origin_id=candidate.origin_id,
                        query=query,
                        embedding_encoder=embedding_encoder,
                        embedding_frames=semantic_frames,
                    )
                    for window_index, window in enumerate(windows):
                        if scene_budget.reason() if scene_budget else len(ids) >= max_results:
                            break
                        normalized = normalize_media(
                            raw,
                            temp / f"normalized-{window_index}.mp4",
                            start_seconds=window.start,
                            max_duration_seconds=window.duration,
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
                        if scene_budget and not scene_budget.valid_media(
                            normalized.media.duration_seconds,
                            normalized.media.width,
                            normalized.media.height,
                        ):
                            errors.append(f"{candidate.origin_id}:media_filter")
                            continue
                        thumb_path = create_thumbnail(
                            normalized.path, temp / f"thumbnail-{window_index}.webp"
                        )
                        storyboard_paths = create_storyboard(
                            normalized.path, temp / f"storyboard-{window_index}"
                        )
                        digest = sha256_file(normalized.path)
                        frame_hash = perceptual_hash(normalized.path)
                        # Optimistic read before expensive release upload; writer rechecks on PUT.
                        remote, _ = writer.read_catalog()
                        draft = {
                            "source": {
                                "provider": candidate.provider,
                                "provider_id": candidate.provider_id,
                                "source_title": localized.original_title,
                                "source_url": safe_source_url(candidate.source_url),
                                "source_start": window.start,
                                "source_end": window.end,
                            },
                            "media": {
                                "sha256": digest,
                                "perceptual_hash": frame_hash,
                                "duration_seconds": normalized.media.duration_seconds,
                            },
                        }
                        match = remote.duplicate_match(draft, phash_similar=perceptually_similar)
                        if match:
                            record_duplicate(candidate, *match)
                            continue
                        release = storage.get_or_create_batch_release(date.today())
                        reserved = (
                            storage.reserved_ids(release)
                            if hasattr(storage, "reserved_ids")
                            else set()
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
                                    "source_url": safe_source_url(candidate.source_url),
                                    "source_start": window.start,
                                    "source_end": window.end,
                                    **(
                                        {
                                            key: candidate.metadata[key]
                                            for key in ("commons_category", "commons_place_aliases")
                                            if candidate.metadata and key in candidate.metadata
                                        }
                                        if candidate.provider == "wikimedia_commons"
                                        else {}
                                    ),
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
                                    name_thumb,
                                    thumb_path.stat().st_size,
                                    thumbnail_digest,
                                    release.id,
                                )
                                # Preview frames use the same verified immutable Releases
                                # pipeline as both the MP4 and its thumbnail.
                                story_assets = []
                                for image_index, preview in enumerate(storyboard_paths):
                                    name_story = f"story_{identifier}_{image_index:02d}.webp"
                                    preview_digest = sha256_file(preview)
                                    storage.upload_asset(release, preview, name_story)
                                    verified = storage.verify_asset(
                                        name_story,
                                        preview.stat().st_size,
                                        preview_digest,
                                        release.id,
                                    )
                                    story_assets.append(verified)
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
                            clip["storage"]["storyboard_urls"] = [
                                asset.browser_download_url for asset in story_assets
                            ]
                            try:
                                writer.update(
                                    [clip],
                                    verified_assets={identifier: (video, thumbnail, *story_assets)},
                                    phash_similar=perceptually_similar,
                                )
                                ids.append(identifier)
                                published_providers.add(candidate.provider)
                                samples = semantic_frames.get(candidate.origin_id)
                                if samples is not None:
                                    try:
                                        vector = clip_vector(
                                            samples, start=window.start, end=window.end
                                        )
                                        embeddings_store.put(identifier, vector)
                                        for store_name, warning in getattr(
                                            embeddings_store, "write_warnings", {}
                                        ).items():
                                            job.setdefault("source_warnings", {})[
                                                f"{candidate.origin_id}:{store_name}"
                                            ] = warning
                                        if os.environ.get("CLIPOTECA_PERSIST_JOBS") == "1":
                                            if remote_embeddings is None:
                                                remote_embeddings = GitHubEmbeddingWriter(
                                                    repository, token
                                                )
                                            if m21_sidecars:
                                                try:
                                                    remote_embeddings.publish(identifier, vector)
                                                except Exception:
                                                    job.setdefault("source_warnings", {})[
                                                        f"{candidate.origin_id}:m21_remote"
                                                    ] = "m21_remote_sidecar_failed:clip_published"
                                            pending_remote_vectors.append((identifier, vector))
                                    except Exception:
                                        # The catalogue and verified MP4 are already durable.
                                        # An optional embedding must not reverse publication.
                                        job.setdefault("source_warnings", {})[
                                            candidate.origin_id
                                        ] = "embeddings_persist_failed:clip_published"
                                storage_bytes += (
                                    video.size + thumbnail.size + sum(a.size for a in story_assets)
                                )
                                published_seconds += time.monotonic() - publish_started
                                catalogue.merge([clip])
                                break
                            except IdentityCollision:
                                reserved.add(identifier)
                                remote, _ = writer.read_catalog()
                                continue
                            except DuplicateClip as duplicate:
                                # Writer's optimistic CAS detected a concurrent
                                # publication. Re-read before attributing provenance.
                                remote, _ = writer.read_catalog()
                                match = remote.duplicate_match(
                                    draft, phash_similar=perceptually_similar
                                )
                                if match and match[0]["id"] == duplicate.clip_id:
                                    record_duplicate(candidate, *match)
                                else:
                                    errors.append(
                                        f"{candidate.origin_id}:duplicate:"
                                        f"{duplicate.clip_id}:concurrent_catalog"
                                    )
                                    duplicate_ids = job.setdefault("duplicate_ids", [])
                                    if duplicate.clip_id not in duplicate_ids:
                                        duplicate_ids.append(duplicate.clip_id)
                                    job["source_errors"][candidate.origin_id] = (
                                        f"duplicate:concurrent_catalog:{duplicate.clip_id}"
                                    )
                                break
                        else:
                            errors.append(f"{candidate.origin_id}:identity_collision")
                except SceneQualityRejected:
                    failed_count += 1
                    job["source_errors"][candidate.origin_id] = "scene_quality_rejected"
                    errors.append(f"{candidate.origin_id}:scene_quality_rejected")
                    rejected = job.setdefault("terminal_sources", [])
                    if candidate_url not in rejected:
                        rejected.append(candidate_url)
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
                    if isinstance(exc, DownloadError):
                        http_status = re.fullmatch(r"HTTP ([1-5][0-9]{2})", str(exc))
                        error_code = (
                            "download_error:HTTP " + http_status.group(1)
                            if http_status
                            else "download_error"
                        )
                    elif isinstance(exc, (NormalizeError, ProbeError)):
                        error_code = "ffmpeg_failed"
                    elif isinstance(exc, MetadataTranslationError):
                        error_code = (
                            public_translation_error(str(exc)) or "metadata_translation_error"
                        )
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
        elif errors and all(":scene_quality_rejected" in entry for entry in errors):
            # Quality heuristics can produce false positives. Keep the URLs
            # terminal within this retry window, but permit a fresh search after
            # the same bounded 24 h quarantine used by dead-lettered work.
            job["status"] = "dead_letter"
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
            rid,
            job["status"],
            tuple(ids or job.get("duplicate_ids", ())),
            tuple(errors),
            _embedding_result(job),
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
        # Flush exactly once per job even on cancellation/timeout. Each vector
        # was enqueued only after the catalogue and MP4 were durable.
        if pending_remote_vectors and remote_embeddings is not None:
            try:
                GitHubShards(remote_embeddings.client, repository).publish_many(
                    pending_remote_vectors
                )
            except Exception:
                job.setdefault("source_warnings", {})["embedding_m22_remote"] = (
                    "shard_batch_persist_failed:clips_published"
                )
            finally:
                pending_remote_vectors.clear()
                try:
                    jobs.save(job)
                except Exception:
                    # Never mask the original CancelledError or publication.
                    pass
        if remote_embeddings is not None:
            remote_embeddings.close()
        if own_storage:
            storage.close()
