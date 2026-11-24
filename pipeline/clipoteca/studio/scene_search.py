"""Bounded progressive discovery for temporary scene imports only."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from ..engine import SearchEngine, SearchResult
from ..ingest import IngestReport
from ..ingest import ingest as _ingest
from ..providers.base import SupportStatus
from ..translate.argos import QUALITY_TRANSLATION_REASONS, public_translation_error
from ..translate.expand import expand_query
from . import library
from .related import primary_queries, related_parent, related_search_query, related_terms
from .windows import fit_mode

_LANGUAGES = ("en", "es", "zh", "ru")
_DOWNLOAD = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
_SEARCHABLE = {_DOWNLOAD, SupportStatus.SUPPORTED_SEARCH_ONLY}


@dataclass
class SceneBudget:
    job: dict
    records: list[dict]
    maximum: int
    enough: int
    deadline: float
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    searches_planned: int = 0
    searches_started: int = 0
    searches_cancelled: int = 0
    downloads_started: int = 0
    downloads_completed: int = 0
    text_checked: dict[str, bool] = field(default_factory=dict)
    discovered: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)
    visual_match: Callable[[dict], bool] | None = None
    visual_cache: dict[str, bool] = field(default_factory=dict)
    commons_consulted: bool = False
    commons_exhausted: bool = False
    rejections: dict[str, int] = field(default_factory=dict)
    rejected: set[tuple[str, str]] = field(default_factory=set)

    @property
    def place(self):
        queries = self.job["queries"]
        return bool(library.query_names(queries["es"] + queries["en"]))

    def valid_media(self, duration, width, height):
        length = float(self.job["len_est"])
        return (
            duration >= 0.42 * length
            and min(width, height) >= self.job.get("min_short_side", 720)
            and fit_mode(duration, length) is not None
        )

    def valid(self, record):
        media = record["media"]
        return self.valid_media(media["duration_seconds"], media["width"], media["height"])

    def _unique_valid(self):
        seen = set()
        for record in self.records:
            if not self.valid(record):
                continue
            digest = str(record["media"].get("sha256") or id(record)).lower()
            if digest not in seen:
                seen.add(digest)
                yield record

    def valid_count(self):
        return sum(1 for _ in self._unique_valid())

    def _reject(self, record, reason):
        digest = str(record.get("media", {}).get("sha256") or record.get("id") or id(record))
        key = (digest.lower(), reason)
        if key not in self.rejected:
            self.rejected.add(key)
            self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def text_good(self, record):
        # Searchable query terms are never evidence; check actual source/title.
        queries = self.job["queries"]
        primary = primary_queries(queries)
        if not library.sufficient_query_match(record, primary):
            match = library.related_match(record, related_terms(self.job))
            if match is None:
                return False
            score, _ = library.text_score(record, [match["term"]], [], self.job.get("avoid", []))
            unpenalized, _ = library.text_score(record, [match["term"]], [], [])
            return score > 0 or score == unpenalized
        relevant = True
        score, _ = library.text_score(
            {"title": record.get("title"), "source": record.get("source")},
            queries["es"],
            queries["en"],
            self.job.get("avoid", []),
        )
        unpenalized, _ = library.text_score(
            {"title": record.get("title"), "source": record.get("source")},
            queries["es"],
            queries["en"],
            [],
        )
        return relevant and (score > 0 or score == unpenalized)

    def provenance(self, query):
        terms = primary_queries(self.job["queries"])
        match = next(
            (
                row
                for row in related_terms(self.job)
                if related_search_query(self.job, row["term"]) == query
            ),
            None,
        )
        if match and terms:
            return {"found_by_query": query, "related_to": related_parent(self.job) or terms[0]}
        return {"found_by_query": query} if related_terms(self.job) else {}

    def candidate_text_good(self, candidate, localized):
        key = candidate.origin_id
        if key not in self.text_checked:
            source = {"source_title": localized.original_title}
            if candidate.provider == "wikimedia_commons":
                source.update(
                    {
                        name: candidate.metadata[name]
                        for name in ("commons_category", "commons_place_aliases")
                        if candidate.metadata and name in candidate.metadata
                    }
                )
            self.text_checked[key] = self.text_good(
                {"title": localized.title, "description": localized.description, "source": source}
            )
            if not self.text_checked[key]:
                self._reject({"id": key}, "text_gate")
        return self.text_checked[key]

    def good(self, record):
        media = record["media"]
        length = float(self.job["len_est"])
        if (
            media["duration_seconds"] < 0.42 * length
            or fit_mode(media["duration_seconds"], length) is None
        ):
            self._reject(record, "too_short")
            return False
        if min(media["width"], media["height"]) < self.job.get("min_short_side", 720):
            self._reject(record, "low_resolution")
            return False
        if not self.text_good(record):
            self._reject(record, "text_gate")
            return False
        digest = str(media.get("sha256") or id(record)).lower()
        if digest not in self.visual_cache:
            if self.clock() >= self.deadline or self.visual_match is None:
                self._reject(record, "timeout_before_check")
                return False
            self.visual_cache[digest] = bool(self.visual_match(record))
        return self.visual_cache[digest]

    def relevant_count(self):
        return sum(self.good(record) for record in self._unique_valid())

    def reason(self):
        if self.clock() >= self.deadline:
            return "timeout"
        if self.place and not (self.commons_consulted or self.commons_exhausted):
            return None
        count = self.relevant_count()
        if count >= self.maximum:
            return "max_results"
        if self.enough and count >= self.enough:
            return "enough"
        return None


def _ordered(adapters, *, place=False):
    return sorted(
        adapters,
        key=lambda adapter: (
            adapter.support_status != _DOWNLOAD,
            not (place and adapter.name == "wikimedia_commons"),
            -int(getattr(adapter, "discovery_priority", 0)),
            adapter.name == "youtube",
        ),
    )


async def ingest(query, adapters, *, scene_budget: SceneBudget, **kwargs):
    """Search bounded parallel waves, consume priority results, and cancel the rest."""
    expansion = expand_query(query, translator=kwargs.get("translator"))
    # Preserve the normal ingest translation gate, including its error reporting.
    if any(
        (not expansion.terms.get(lang) or lang in expansion.errors)
        and (
            expansion.terms.get(lang)
            or (public_translation_error(expansion.errors.get(lang, "")) or "").rsplit(":", 1)[-1]
            not in QUALITY_TRANSLATION_REASONS
        )
        for lang in _LANGUAGES
    ):
        return await _ingest(
            query,
            adapters,
            discovery=SearchResult(expansion, [], [], [], 0),
            scene_budget=scene_budget,
            **kwargs,
        )

    writer = kwargs["writer"]
    horizon = kwargs.pop("horizon")
    limit = kwargs.pop("max_results")
    concurrency = kwargs["concurrency"]
    errors = []
    ids = []
    statuses = []
    last = "scene"
    seen = set()
    index = 0
    ordered = _ordered(adapters, place=scene_budget.place)

    async def discover(adapter, lang):
        if adapter.support_status in _SEARCHABLE:
            scene_budget.searches_started += 1
        try:
            partial = replace(expansion, terms={lang: expansion.terms[lang]})
            result = await SearchEngine([adapter], concurrency=1).search(
                query,
                catalog=writer.read_catalog()[0],
                expansion=partial,
                allow_partial_expansion=True,
            )
            if adapter.support_status == _DOWNLOAD:
                scene_budget.discovered.update(c.origin_id for c in result.candidates)
            return replace(result, expansion=expansion)
        except asyncio.CancelledError:
            scene_budget.searches_cancelled += 1
            raise

    # For a place, await Commons category discovery before any stock-provider wave.
    plan = []
    first = None
    if scene_budget.place and not scene_budget.commons_consulted:
        first = next(
            (
                (lang, adapter)
                for lang in _LANGUAGES
                for adapter in ordered
                if adapter.name == "wikimedia_commons"
                and adapter.support_status in _SEARCHABLE
                and lang in adapter.accepts_text_languages
                and expansion.terms.get(lang)
            ),
            None,
        )
        if first is None:
            scene_budget.commons_exhausted = True
        else:
            plan.append((first[0], [first[1]]))
    for lang in _LANGUAGES:
        if not expansion.terms.get(lang):
            continue
        available = [a for a in ordered if lang in a.accepts_text_languages and (lang, a) != first]
        for offset in range(0, len(available), concurrency):
            plan.append((lang, available[offset : offset + concurrency]))

    for lang, batch in plan:
        if scene_budget.reason():
            break
        tasks = [asyncio.create_task(discover(adapter, lang)) for adapter in batch]
        try:
            for adapter, task in zip(batch, tasks, strict=True):
                if scene_budget.reason():
                    break
                result = await task
                # Discovery has completed; media processing may still time out.
                if (
                    scene_budget.place
                    and adapter.name == "wikimedia_commons"
                    and adapter.support_status in _SEARCHABLE
                ):
                    scene_budget.commons_consulted = True
                if scene_budget.reason():
                    break
                unique = [c for c in result.candidates if c.origin_id not in seen]
                seen.update(c.origin_id for c in unique)
                result = replace(result, candidates=unique)
                report = await _ingest(
                    query,
                    adapters,
                    max_results=limit,
                    horizon=f"{horizon}-batch-{index}",
                    discovery=result,
                    scene_budget=scene_budget,
                    **kwargs,
                )
                # Evaluate the final candidate too, even if no other wave follows.
                scene_budget.relevant_count()
                index += 1
                last = report.request_id
                errors.extend(report.errors)
                statuses.append(report.status)
                if report.status == "published":
                    ids.extend(report.published_ids)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    status = (
        "published"
        if ids
        else next(
            (s for s in statuses if s not in {"no_results", "duplicate", "unavailable"}),
            "no_results" if "no_results" in statuses or not statuses else statuses[0],
        )
    )
    return IngestReport(last, status, tuple(ids), tuple(errors))


async def ingest_related(queries, adapters, *, scene_budget, on_query, **kwargs):
    """Parallel discovery across distinct terms; serialize every ingest/catalog write."""
    writer = kwargs["writer"]
    horizon = kwargs.pop("horizon")
    limit = kwargs.pop("max_results")
    concurrency = kwargs["concurrency"]
    ordered = _ordered(adapters, place=scene_budget.place)
    if scene_budget.place:
        ordered.sort(key=lambda adapter: adapter.name != "wikimedia_commons")
    expansions = []
    errors = []
    ids = []
    statuses = []
    seen = set()
    last = "scene-related"
    index = 0

    for term in queries:
        if scene_budget.reason():
            break
        query = related_search_query(scene_budget.job, term)
        expansion = expand_query(query, translator=kwargs.get("translator"))
        invalid = any(
            (not expansion.terms.get(lang) or lang in expansion.errors)
            and (
                expansion.terms.get(lang)
                or (public_translation_error(expansion.errors.get(lang, "")) or "").rsplit(":", 1)[
                    -1
                ]
                not in QUALITY_TRANSLATION_REASONS
            )
            for lang in _LANGUAGES
        )
        if invalid:
            on_query(term)
            report = await _ingest(
                query,
                adapters,
                max_results=limit,
                horizon=f"{horizon}-translation-{index}",
                discovery=SearchResult(expansion, [], [], [], 0),
                scene_budget=scene_budget,
                **kwargs,
            )
            index += 1
            errors.extend(report.errors)
            statuses.append(report.status)
        else:
            expansions.append((term, query, expansion))

    plan = [
        (term, query, expansion, adapter, lang)
        for lang in _LANGUAGES
        for adapter in ordered
        for term, query, expansion in expansions
        if expansion.terms.get(lang) and lang in adapter.accepts_text_languages
    ]
    if scene_budget.place and not any(
        adapter.name == "wikimedia_commons" and adapter.support_status in _SEARCHABLE
        for _, _, _, adapter, _ in plan
    ):
        scene_budget.commons_exhausted = True

    async def discover(term, query, expansion, adapter, lang):
        on_query(term)
        if adapter.support_status in _SEARCHABLE:
            scene_budget.searches_started += 1
        try:
            partial = replace(expansion, terms={lang: expansion.terms[lang]})
            result = await SearchEngine([adapter], concurrency=1).search(
                query,
                catalog=writer.read_catalog()[0],
                expansion=partial,
                allow_partial_expansion=True,
            )
            if adapter.support_status == _DOWNLOAD:
                scene_budget.discovered.update(c.origin_id for c in result.candidates)
            return replace(result, expansion=expansion)
        except asyncio.CancelledError:
            scene_budget.searches_cancelled += 1
            raise

    for offset in range(0, len(plan), concurrency):
        if scene_budget.reason():
            break
        batch = plan[offset : offset + concurrency]
        tasks = [asyncio.create_task(discover(*entry)) for entry in batch]
        try:
            for (_, query, _, adapter, _), task in zip(batch, tasks, strict=True):
                if scene_budget.reason():
                    break
                result = await task
                if adapter.name == "wikimedia_commons" and adapter.support_status in _SEARCHABLE:
                    scene_budget.commons_consulted = True
                if scene_budget.reason():
                    break
                unique = [
                    candidate for candidate in result.candidates if candidate.origin_id not in seen
                ]
                seen.update(candidate.origin_id for candidate in unique)
                report = await _ingest(
                    query,
                    adapters,
                    max_results=max(1, limit - scene_budget.relevant_count()),
                    horizon=f"{horizon}-batch-{index}",
                    discovery=replace(result, candidates=unique),
                    scene_budget=scene_budget,
                    **kwargs,
                )
                scene_budget.relevant_count()
                index += 1
                last = report.request_id
                errors.extend(report.errors)
                statuses.append(report.status)
                if report.status == "published":
                    ids.extend(report.published_ids)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    status = (
        "published"
        if ids
        else next(
            (
                status
                for status in statuses
                if status not in {"no_results", "duplicate", "unavailable"}
            ),
            "no_results",
        )
    )
    return IngestReport(last, status, tuple(ids), tuple(errors))
