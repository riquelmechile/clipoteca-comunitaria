"""M28 PR3: strictly local ingest adapter for temporary studio scene candidates."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from ..catalog import Catalog
from ..providers.base import SupportStatus
from ..sources import SOURCES
from ..storage.github import ReleaseAsset, ReleaseBatch, StorageError
from . import library
from .plan import _atomic_json
from .scene_search import SceneBudget, ingest
from .windows import fit_mode


class ImportSceneError(ValueError):
    """Unsafe input or local import contract cannot be satisfied."""


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_OWNED = ("clips", "thumbs", "storyboards", ".ingest-local")
_DISABLED = ("CLIPOTECA_PERSIST_JOBS", "CLIPOTECA_M21_SIDECARS")


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ImportSceneError(f"{field}: invalid numeric value")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ImportSceneError(f"{field}: expected positive finite value")
    return number


def _validate(job: Any, max_results: int, max_clips: int, concurrency: int):
    if not isinstance(job, dict) or not isinstance(job.get("id"), str):
        raise ImportSceneError("job.id missing")
    if not _ID.fullmatch(job["id"]) or ".." in job["id"]:
        raise ImportSceneError("job.id unsafe")
    if job.get("aspect") not in {"9:16", "16:9", "1:1"}:
        raise ImportSceneError("job.aspect invalid")
    length = _positive(job.get("len_est"), "job.len_est")
    fetch = _positive(job.get("len_fetch"), "job.len_fetch")
    if not isinstance(job.get("prompt"), str) or not job["prompt"].strip():
        raise ImportSceneError("job.prompt missing")
    queries = job.get("queries")
    if not isinstance(queries, dict):
        raise ImportSceneError("job.queries invalid")
    distinct: list[str] = []
    seen: set[str] = set()
    for lang in ("en", "es"):
        terms = queries.get(lang)
        if not isinstance(terms, list) or any(
            not isinstance(item, str) or not item.strip() for item in terms
        ):
            raise ImportSceneError(f"job.queries.{lang} invalid")
        for text in terms:
            term = text.strip()
            if term.casefold() not in seen:
                seen.add(term.casefold())
                distinct.append(term)
    minimum = job.get("min_short_side", 720)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ImportSceneError("job.min_short_side invalid")
    if (
        not isinstance(max_results, int)
        or isinstance(max_results, bool)
        or not 1 <= max_results <= 50
    ):
        raise ImportSceneError("max_results must be 1..50")
    if not isinstance(max_clips, int) or isinstance(max_clips, bool) or not 1 <= max_clips <= 3:
        raise ImportSceneError("max_clips_per_source must be 1..3")
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= 16
    ):
        raise ImportSceneError("concurrency must be 1..16")
    return length, fetch, distinct


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as target, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


class _SceneDeadline(BaseException):
    """Interrupt synchronous media work as well as asynchronous discovery."""


@contextmanager
def _wall_deadline(seconds):
    # Actions invokes this on the POSIX main thread. Do not replace a caller's
    # existing alarm or install signal handlers in a non-main thread.
    if (
        not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
        or any(signal.getitimer(signal.ITIMER_REAL))
    ):
        yield
        return

    active = True

    def expire(_signum, _frame):
        if active:
            raise _SceneDeadline()

    previous = signal.signal(signal.SIGALRM, expire)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
            yield
        finally:
            active = False
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        # The outer guard also runs if the alarm interrupts entry into the
        # inner finally. Ignore any already-dispatched alarm during cleanup.
        active = False
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous)


@contextmanager
def _no_remote_writes():
    # ingest reads these dynamically. Restore absent and present values alike.
    before = {name: os.environ.get(name) for name in _DISABLED}
    try:
        os.environ["CLIPOTECA_PERSIST_JOBS"] = "0"
        os.environ["CLIPOTECA_M21_SIDECARS"] = "0"
        yield
    finally:
        for name, prior in before.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


class LocalWriter:
    """An entirely in-memory GitHubCatalogWriter replacement."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.imported: list[dict[str, Any]] = []

    def read_catalog(self) -> tuple[Catalog, str]:
        return self.catalog, "local"

    def update(
        self,
        incoming: Sequence[dict[str, Any]],
        *,
        verified_assets=None,
        phash_similar=None,
        retries: int = 3,
    ) -> str:
        for clip in incoming:
            assets = (verified_assets or {}).get(clip["id"], ())
            if len(assets) < 2 or any(a.state != "uploaded" for a in assets):
                raise StorageError("unverified local assets")
        self.catalog.merge(incoming)
        self.imported.extend(incoming)
        return "local"


@dataclass
class _Stored:
    path: Path
    asset: ReleaseAsset


class LocalStorage:
    """ReleaseStorage duck type; bytes always remain inside the provided workspace."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.assets: dict[str, _Stored] = {}
        self.by_id: dict[int, str] = {}

    def get_or_create_batch_release(self, day: date) -> ReleaseBatch:
        return ReleaseBatch(1, "studio-local", "local://uploads")

    def reserved_ids(self, release: ReleaseBatch) -> set[str]:
        return set()

    def _asset_path(self, name: str) -> Path:
        if not re.fullmatch(r"(?:clip|thumb|story)_clip-\d{6,}(?:_\d{2})?\.(?:mp4|webp)", name):
            raise StorageError("unsafe local asset name")
        return self.directory / name

    def find_existing_asset(
        self,
        release: ReleaseBatch,
        name: str,
        size: int,
        digest: str,
    ) -> ReleaseAsset | None:
        old = self.assets.get(name)
        if old is None:
            return None
        if old.asset.size == size and hashlib.sha256(old.path.read_bytes()).hexdigest() == digest:
            return old.asset
        raise StorageError("local asset name collision")

    def upload_asset(self, release: ReleaseBatch, path: Path, name: str) -> ReleaseAsset:
        data_sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        size = Path(path).stat().st_size
        old = self.find_existing_asset(release, name, size, data_sha)
        if old is not None:
            return old
        target = self._asset_path(name)
        _atomic_copy(Path(path), target)
        asset = ReleaseAsset(
            len(self.assets) + 1,
            name,
            target.stat().st_size,
            "uploaded",
            "local://assets/" + name,
        )
        self.assets[name] = _Stored(target, asset)
        self.by_id[asset.id] = name
        return asset

    def verify_asset(self, name: str, size: int, digest: str, release_id: int) -> ReleaseAsset:
        if release_id != 1 or name not in self.assets:
            raise StorageError("missing local asset")
        stored = self.assets[name]
        if (
            stored.path.stat().st_size != size
            or hashlib.sha256(stored.path.read_bytes()).hexdigest() != digest
        ):
            raise StorageError("local asset digest or size mismatch")
        return stored.asset

    def delete_asset(self, asset_id: int) -> None:
        name = self.by_id.pop(asset_id, None)
        if name:
            self.assets.pop(name).path.unlink(missing_ok=True)

    def close(self) -> None:
        pass


def _catalog(path: Path) -> Catalog:
    if not path.is_file():
        raise ImportSceneError("catalog_path missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        clips = data.get("clips") if isinstance(data, dict) else data
        if not isinstance(clips, list):
            raise ValueError("invalid catalog")
        return Catalog(clips)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise ImportSceneError("catalog_path invalid") from exc


def _adapters(providers: Sequence[str] | str | None):
    # Reuse the CLI source definitions, credential/unavailable adapters and runner routing.
    from ..cli import _MissingCredentialsAdapter, _UnavailableAdapter
    from ..runner import selected_providers

    names = {spec.name for spec in SOURCES}
    requested = (
        {part.strip() for part in providers.split(",") if part.strip()}
        if isinstance(providers, str)
        else set(providers or ())
    )
    if requested - names:
        raise ImportSceneError("unknown provider")
    repository = Path(__file__).resolve().parents[3]
    available = selected_providers(
        "actions",
        requested,
        config_path=repository / "data/runner-routing.json",
    )
    built = []
    for spec in SOURCES:
        if spec.name not in available:
            continue
        if any(not os.environ.get(key) for key in spec.requires):
            built.append(_MissingCredentialsAdapter(spec.name, spec.adapter.accepts_text_languages))
            continue
        try:
            built.append(spec.adapter())
        except Exception as exc:
            built.append(
                _UnavailableAdapter(
                    spec.name,
                    spec.adapter.accepts_text_languages,
                    type(exc).__name__,
                )
            )
    return built


# Candidates the engine looked at and turned down: the search worked, there was just nothing
# usable. They never make a run an "error"; only real failures do.
_SOFT_REJECTIONS = (
    "duplicate",
    "search_only",
    "aspect_filter",
    "media_filter",
    "language_filter",
    "scene_quality_rejected",
    "identity_collision",
)
_QUIET_STATUSES = frozenset({"published", "duplicate", "no_results"})


def _classify(errors: Sequence[str], rejections: dict[str, int]) -> list[str]:
    """Count soft rejections and return the class names of real failures.

    Upstream entries look like "origin:code[:detail]" and may carry candidate URLs, so only
    a known soft code or a bare identifier-like last segment is ever kept.
    """
    hard: list[str] = []
    for error in errors:
        parts = str(error).split(":")
        soft = next((code for code in _SOFT_REJECTIONS if code in parts), None)
        if soft:
            rejections[soft] = rejections.get(soft, 0) + 1
            continue
        name = parts[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,70}", name):
            hard.append(name)
        else:
            hard.append("ingest_error")
    return hard


async def _close_adapters(adapters: Sequence[Any]) -> None:
    for adapter in adapters:
        client = getattr(adapter, "client", None)
        if client is not None:
            close = getattr(client, "aclose", None)
            if close:
                await close()


def _manifest_clip(
    record: dict[str, Any],
    storage: LocalStorage,
    out: Path,
) -> dict[str, Any]:
    media = record["media"]
    digest = str(media["sha256"]).lower()
    ident = "imp-" + digest[:12]
    old_id = record["id"]
    assets: dict[str, Any] = {}
    video_name = f"clip_{old_id}.mp4"
    thumb_name = f"thumb_{old_id}.webp"
    for name, rel, field in (
        (video_name, f"clips/{ident}.mp4", "video"),
        (thumb_name, f"thumbs/{ident}.webp", "thumbnail"),
    ):
        stored = storage.assets[name]
        _atomic_copy(stored.path, out / rel)
        assets[field] = rel
    storyboard: list[str] = []
    prefix = f"story_{old_id}_"
    for name in sorted(storage.assets):
        if name.startswith(prefix):
            relative = f"storyboards/{ident}_{name[len(prefix) :]}"
            _atomic_copy(storage.assets[name].path, out / relative)
            storyboard.append(relative)
    assets["storyboard"] = storyboard
    # Ingest uses the wall clock for its eventual publication timestamp.
    # This is a provisional, reproducible record. M32 sets the actual publication date.
    record = json.loads(json.dumps(record))
    record["created_at"] = "1970-01-01T00:00:00Z"
    return {
        "imp_id": ident,
        "sha256": digest,
        "size_bytes": media["size_bytes"],
        "duration": media["duration_seconds"],
        "width": media["width"],
        "height": media["height"],
        "local_path": assets["video"],
        "source_url": record["source"]["source_url"],
        "provider": record["source"]["provider"],
        "record": record,
        "assets": assets,
    }


def _assignment(job: dict[str, Any], clips: list[dict[str, Any]]):
    length = float(job["len_est"])
    scored = []
    for item in clips:
        if item["duration"] < 0.42 * length:
            continue
        if min(item["width"], item["height"]) < job.get("min_short_side", 720):
            continue
        fit = fit_mode(float(item["duration"]), length)
        if fit is None:
            continue
        score, matched = library.text_score(
            item["record"],
            job["queries"]["es"],
            job["queries"]["en"],
            job.get("avoid", []),
        )
        scored.append(
            {
                "clip_id": item["imp_id"],
                "score": score,
                "matched": matched,
                "name_match": library.name_match(
                    item["record"], job["queries"]["es"] + job["queries"]["en"]
                ),
                "duration": item["duration"],
                "width": item["width"],
                "height": item["height"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "source_url": item["source_url"],
                "fit": fit,
                "local_path": item["local_path"],
            }
        )
    scored.sort(
        key=lambda x: (not x["name_match"], -x["score"], abs(x["duration"] - length), x["clip_id"])
    )
    return {
        "id": job["id"],
        "status": "imported" if scored else "needs_import",
        "primary": scored[0] if scored else None,
        "backups": scored[1:],
    }


_QUERY_STOP = frozenset(
    "a an the of in on at and or with from for footage real natural moving view close "
    "up cinematic beautiful detailed high quality shot video aerial slow motion "
    "el la los las de del en y con por para un una vista video imagen hermoso".split()
)


def simple_queries(job, original):
    """Derive up to two short subject queries, without a language model."""
    used = {term.casefold() for term in original}
    result = []
    for text in (job.get("title", ""), job["prompt"]):
        words = [
            word
            for word in re.findall(r"[^\W\d_]+", text.casefold())
            if word not in _QUERY_STOP and len(word) > 1
        ]
        for term in (" ".join(words[:3]), " ".join(words[:1])):
            if term and term not in used:
                result.append(term)
                used.add(term)
                break
    return result


def import_scene(
    job: dict[str, Any],
    out_dir: str | Path,
    *,
    catalog_path: str | Path,
    adapters: Sequence[Any] | None = None,
    providers: Sequence[str] | str | None = None,
    max_results: int = 3,
    max_clips_per_source: int = 2,
    concurrency: int = 8,
    downloader: Callable[..., Any] | None = None,
    translator: Callable[..., Any] | None = None,
    embedding_encoder: Any = None,
    timeout_s: float | None = None,
    spread_queries: bool = False,
    simple: bool = False,
    enough: int = 3,
) -> dict[str, Any]:
    """Search using existing ingest but keep ALL results, jobs and assets local."""
    length, fetch, queries = _validate(job, max_results, max_clips_per_source, concurrency)
    if isinstance(enough, bool) or not isinstance(enough, int) or not 0 <= enough <= 50:
        raise ImportSceneError("enough must be 0..50")
    if simple:
        queries = simple_queries(job, queries)
    # Stable ties retain the original English-first order.
    queries.sort(key=lambda term: -len(set(library._words(term)) - _QUERY_STOP))
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("CLIPOTECA_IMPORT_TIMEOUT", "900"))
        except ValueError as exc:
            raise ImportSceneError("CLIPOTECA_IMPORT_TIMEOUT invalid") from exc
    timeout = _positive(timeout_s, "timeout_s")
    if adapters is not None and providers is not None:
        raise ImportSceneError("adapters and providers are mutually exclusive")
    catalog = _catalog(Path(catalog_path))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for name in _OWNED:
        location = out / name
        if location.is_symlink():
            raise ImportSceneError("unsafe output directory")
        if location.is_dir():
            shutil.rmtree(location)
        elif location.exists():
            location.unlink()
    for name in ("imported.json", "assignment.json"):
        previous = out / name
        if previous.is_symlink():
            raise ImportSceneError("unsafe output manifest")
        previous.unlink(missing_ok=True)
    writer = LocalWriter(catalog)
    stage = out / ".ingest-local"
    storage = LocalStorage(stage / "assets")
    tried: list[str] = []
    failures: list[str] = []
    rejections: dict[str, int] = {}
    own_adapters = adapters is None
    selected = _adapters(providers) if own_adapters else adapters
    duration_cap = min(20, max(8, math.ceil(fetch) + 4))
    budget = SceneBudget(job, writer.imported, max_results, enough, started + timeout)
    budget.searches_planned = len(queries) * sum(
        len(set(adapter.accepts_text_languages) & {"en", "es", "zh", "ru"})
        for adapter in selected
        if adapter.support_status == SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
        or adapter.support_status == SupportStatus.SUPPORTED_SEARCH_ONLY
    )

    async def execute() -> None:
        try:
            if not selected:
                return
            for index, query in enumerate(queries):
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    failures.append("timeout")
                    break
                if budget.reason():
                    break
                tried.append(query)
                # The legacy spread flag remains accepted, but never reserves slots
                # for later queries once adequate material is available.
                slots = max_results - budget.valid_count()
                try:
                    report = await asyncio.wait_for(
                        ingest(
                            query,
                            selected,
                            repository="local/local",
                            token="",
                            job_root=stage / "jobs",
                            max_results=slots,
                            scene_budget=budget,
                            max_clips_per_source=max_clips_per_source,
                            concurrency=concurrency,
                            # No aspect filter: clip-scene reframes any orientation to the
                            # target, and filtering here would discard most horizontal stock.
                            filters={"max_duration": duration_cap},
                            horizon=f"studio-v1-{index}"
                            if not simple
                            else f"studio-simple-{index}",
                            writer=writer,
                            storage=storage,
                            downloader=downloader,
                            translator=translator,
                            embedding_encoder=embedding_encoder,
                        ),
                        timeout=remaining,
                    )
                    hard = _classify(report.errors, rejections)
                    failures.extend(hard)
                    soft_only = bool(report.errors) and not hard
                    if report.status not in _QUIET_STATUSES and not soft_only:
                        failures.append("ingest_" + report.status)
                except TimeoutError:
                    failures.append("timeout")
                    break
                except Exception as exc:
                    failures.append(type(exc).__name__)
        finally:
            if own_adapters:
                await _close_adapters(selected)

    with _no_remote_writes():
        try:
            with _wall_deadline(timeout - (time.monotonic() - started)):
                asyncio.run(execute())
        except _SceneDeadline:
            failures.append("timeout")

    reason = "timeout" if "timeout" in failures else (budget.reason() or "exhausted")
    imported: list[dict[str, Any]] = []
    seen_digests: set[str] = set()
    for record in writer.imported:
        digest = str(record["media"]["sha256"]).lower()
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        imported.append(_manifest_clip(record, storage, out))
    imported.sort(key=lambda x: x["imp_id"])
    assignment = _assignment(job, imported)
    report = {
        "version": 1,
        "id": job["id"],
        "status": "ok" if imported else ("error" if failures else "no_results"),
        "queries_tried": tried,
        "clips": imported,
        "errors": sorted(set(failures)),
        "rejections": dict(sorted(rejections.items())),
        "time_s": round(time.monotonic() - started, 3),
        "termination_reason": reason,
        "searches_started": budget.searches_started,
        "searches_avoided": max(0, budget.searches_planned - budget.searches_started),
        "searches_cancelled": budget.searches_cancelled,
        "downloads_started": budget.downloads_started,
        "downloads_avoided": len(budget.discovered - budget.attempted),
    }
    _atomic_json(out / "imported.json", report)
    _atomic_json(out / "assignment.json", assignment)
    shutil.rmtree(stage, ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipoteca.studio.importer")
    command = parser.add_subparsers(dest="command", required=True)
    scene = command.add_parser("scene")
    scene.add_argument("--job", type=Path, required=True)
    scene.add_argument("--catalog", type=Path, required=True)
    scene.add_argument("--out", type=Path, required=True)
    scene.add_argument("--providers")
    scene.add_argument("--max-results", type=int, default=3)
    scene.add_argument("--enough", type=int, default=3)
    scene.add_argument("--spread-queries", action="store_true")
    scene.add_argument("--simple", action="store_true")
    scene.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        result = import_scene(
            job,
            args.out,
            catalog_path=args.catalog,
            providers=args.providers,
            max_results=args.max_results,
            spread_queries=args.spread_queries,
            simple=args.simple,
            enough=args.enough,
            timeout_s=args.timeout,
        )
    except (OSError, ImportSceneError, ValueError, TypeError) as exc:
        parser.exit(2, f"importer: {type(exc).__name__}: invalid input\n")
    print(
        json.dumps(
            {
                "id": result["id"],
                "status": result["status"],
                "imported": len(result["clips"]),
                "termination_reason": result["termination_reason"],
                "time_s": result["time_s"],
                "searches_started": result["searches_started"],
                "searches_avoided": result["searches_avoided"],
                "downloads_started": result["downloads_started"],
                "downloads_avoided": result["downloads_avoided"],
            },
            ensure_ascii=False,
        )
    )
    if result["status"] == "error":
        return 1
    return 0 if result["clips"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
