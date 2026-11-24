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
    discovered: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)

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

    def good(self, record):
        # Ingest's localized tags include the search query itself. Counting those
        # would make every hit look relevant. Use the actual source/title instead.
        queries = self.job["queries"]
        relevant = library.sufficient_query_match(record, queries["es"] + queries["en"])
        score, _ = library.text_score(
            {"title": record.get("title"), "source": record.get("source")},
            queries["es"], queries["en"], self.job.get("avoid", []),
        )
        return (
            self.valid(record)
            and record["media"]["duration_seconds"] >= self.job["len_fetch"]
            and relevant
            and (score > 0 or not self.job.get("avoid"))
        )

    def reason(self):
        if self.clock() >= self.deadline:
            return "timeout"
        if self.valid_count() >= self.maximum:
            return "max_results"
        if self.enough and sum(self.good(record) for record in self._unique_valid()) >= self.enough:
            return "enough"
        return None


def _ordered(adapters):
    return sorted(
        adapters,
        key=lambda adapter: (
            adapter.support_status != _DOWNLOAD,
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
    ordered = _ordered(adapters)

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

    for lang in _LANGUAGES:
        if not expansion.terms.get(lang) or scene_budget.reason():
            continue
        available = [a for a in ordered if lang in a.accepts_text_languages]
        for offset in range(0, len(available), concurrency):
            if scene_budget.reason():
                break
            batch = available[offset : offset + concurrency]
            tasks = [asyncio.create_task(discover(adapter, lang)) for adapter in batch]
            try:
                for task in tasks:
                    if scene_budget.reason():
                        break
                    result = await task
                    if scene_budget.reason():
                        break
                    unique = [c for c in result.candidates if c.origin_id not in seen]
                    seen.update(c.origin_id for c in unique)
                    result = replace(result, candidates=unique)
                    slots = min(limit - len(ids), scene_budget.maximum - scene_budget.valid_count())
                    if slots <= 0 or scene_budget.reason():
                        break
                    report = await _ingest(
                        query,
                        adapters,
                        max_results=slots,
                        horizon=f"{horizon}-batch-{index}",
                        discovery=result,
                        scene_budget=scene_budget,
                        **kwargs,
                    )
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
