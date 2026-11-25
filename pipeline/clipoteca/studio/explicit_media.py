"""Select explicit public video material without semantic or OCR filtering."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np

from ..media.probe import probe_media
from ..security.download import DownloadError, download_image, download_media
from . import checks, framing, image_motion
from .clipscene import _atomic_json, _download, _preview_frames, _run

_USER_AGENT = "ClipotecaComunitaria/3.0 (+https://github.com/riquelmechile/clipoteca-comunitaria)"
_REF = re.compile(r"(?:u[1-9a-f][a-f0-9]{0,11}|a[0-9]{4}-[0-9]{2}-[a-f0-9]{32})\Z")


class ExplicitMediaError(ValueError):
    """Safe diagnostic for an explicitly selected clip."""


def registry_record(ref: str) -> dict:
    """Read only trusted public registry locations; never forward a token to an asset."""
    repo = os.environ.get("GITHUB_REPOSITORY", "riquelmechile/clipoteca-comunitaria")
    if not _REF.fullmatch(ref) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ExplicitMediaError("Referencia de aporte no valida.")
    if ref.startswith("u"):
        number = int(ref[1:], 16)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                f"https://api.github.com/repos/{repo}/issues/{number}", headers=headers
            )
            response.raise_for_status()
            issue = response.json()
        if (
            issue.get("number") != number
            or issue.get("pull_request")
            or issue.get("author_association") not in ("OWNER", "MEMBER", "COLLABORATOR")
            or not re.fullmatch(r"\[media-v1\] [a-f0-9]{64}", issue.get("title", ""))
        ):
            raise ExplicitMediaError("Aporte inexistente.")
        row = json.loads(issue["body"])
        if row.get("registry") != "clipoteca-media-v1" or row.get("origin") != "url":
            raise ExplicitMediaError("Aporte inexistente.")
    else:
        tag = "uploads-" + ref[1:8]
        url = f"https://github.com/{repo}/releases/download/{tag}/{ref}.json"
        with httpx.Client(timeout=20, follow_redirects=True, trust_env=False) as client:
            response = client.get(url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            row = response.json()
        if row.get("media_id") != ref or row.get("origin") != "upload":
            raise ExplicitMediaError("Aporte inexistente.")
    if row.get("kind") not in ("video", "image"):
        raise ExplicitMediaError("Tipo de aporte no valido.")
    if row.get("status") != "registered":
        raise ExplicitMediaError("Aporte no registrado.")
    for key in ("url", "title", "author", "source"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ExplicitMediaError("Registro de aporte incompleto.")
    if ref.startswith("a"):
        # Upload bytes have a known immutable release location.
        expected = f"https://github.com/{repo}/releases/download/{tag}/{ref}.media"
        if row["url"] != expected:
            raise ExplicitMediaError("URL de aporte no valida.")
    return row


def window_bounds(media: dict, duration: float, length: float) -> tuple[float, float]:
    start = float(media.get("start", 0))
    end = float(media.get("end", duration))
    if not 0 <= start < end <= duration + 0.04:
        raise ExplicitMediaError("Ventana del aporte fuera de la duracion del video.")
    end = min(end, duration)
    if end - start < length / 1.25:
        raise ExplicitMediaError("Aporte demasiado corto: no se admite pingpong.")
    return start, end


def _starts(path, sw, sh, duration, start, end, span, explicit_start, deadline):
    if explicit_start or end - start <= span + 0.1:
        return [start]
    frames, stamps = _preview_frames(path, sw, sh, duration, deadline)
    # Rank windows by useful brightness and temporal variation, not subject similarity.
    scores = []
    last = max(start, end - span)
    for at in np.linspace(start, last, min(16, max(2, int(last - start) + 1))):
        local = [f for f, t in zip(frames, stamps, strict=True) if at <= t < at + span]
        if len(local) < 2:
            continue
        brightness = float(np.mean([f.mean() for f in local]))
        motion = float(
            np.mean(
                [
                    np.abs(b.astype(float) - a.astype(float)).mean()
                    for a, b in zip(local[:-1], local[1:], strict=True)
                ]
            )
        )
        scores.append((motion - abs(brightness - 128) / 16, float(at)))
    return [at for _, at in sorted(scores, reverse=True)] or [start]


def _transcode(source, target, start, span, vf, deadline):
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(span),
            "-an",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            "-movflags",
            "+faststart",
            str(target),
        ],
        deadline,
    )


def _index_manifest(root, rendered, record, ref, start, duration, aspect, deadline):
    """Use the same verified imported asset contract; index only after video QA."""
    folder = root / "contribution"
    folder.mkdir(exist_ok=True)
    video = folder / "video.mp4"
    thumb = folder / "thumbnail.webp"
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(rendered),
            "-t",
            str(min(duration, 19.95)),
            "-c",
            "copy",
            str(video),
        ],
        deadline,
    )
    width, height, seconds = checks.probe_video(video, deadline=deadline)
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(seconds / 2),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-2",
            str(thumb),
        ],
        deadline,
    )
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    identifier = "imp-" + digest[:12]
    size = video.stat().st_size
    title = record["title"]
    source = {
        "provider": "user_contribution",
        "provider_id": ref,
        "source_language": "und",
        "source_title": title,
        "source_url": record["url"],
        "source_author": record["author"],
        "declared_source": record["source"],
        "source_start": start,
        "source_end": start + seconds,
    }
    if record.get("license_note"):
        source["license_note"] = record["license_note"]
    entry = {
        "imp_id": identifier,
        "sha256": digest,
        "size_bytes": size,
        "local_path": "video.mp4",
        "source_url": record["url"],
        "assets": {"video": "video.mp4", "thumbnail": "thumbnail.webp", "storyboard": []},
        "record": {
            "id": "clip-000000",
            "title": {"es": title, "en": title},
            "description": {"es": record["source"], "en": record["source"]},
            "tags": {"es": [], "en": []},
            "source": source,
            "media": {
                "duration_seconds": seconds,
                "width": width,
                "height": height,
                "fps": 30,
                "aspect_ratio": aspect,
                "orientation": {"9:16": "vertical", "16:9": "horizontal", "1:1": "square"}[aspect],
                "codec": "h264",
                "container": "mp4",
                "has_audio": False,
                "size_bytes": size,
                "sha256": digest,
                "perceptual_hash": checks.mid_phash(video, deadline=deadline),
            },
            "storage": {
                "provider": "github_releases",
                "asset_url": "https://example.invalid/video",
                "thumbnail_url": "https://example.invalid/thumbnail",
            },
            "status": "published",
            "created_at": "1970-01-01T00:00:00Z",
        },
    }
    _atomic_json(folder / "imported.json", {"clips": [entry]})
    return identifier


def _select_image(root, job, ref, record, folder, deadline):
    media = job["media"]
    if "start" in media or "end" in media:
        raise ExplicitMediaError("start/end no aplican a imagenes.")
    repo_asset = ref.startswith("asset:")
    if repo_asset:
        image = image_motion.rasterize_asset(ref)
        source_url = (
            "https://github.com/riquelmechile/clipoteca-comunitaria/blob/main/assets/"
            + ref[6:]
            + ".svg"
        )
        origin = "repo_asset"
    else:
        source_url = record["url"]
        source = asyncio.run(download_image(source_url, folder, user_agent=_USER_AGENT))
        image = image_motion.load_photo(source)
        origin = "contribution_image"
    motion = media.get("motion", "auto")
    mode = media.get("framing", "contain" if repo_asset else "cover")
    canvas = root / "image-canvas.png"
    sw, sh = image_motion.prepare_canvas(image, canvas, job["aspect"], mode, repo_asset=repo_asset)
    rendered = root / "clip.mp4"
    image_motion.render_motion(
        canvas, rendered, job["aspect"], motion, job["len_fetch"], repo_asset=repo_asset
    )
    rw, rh, duration = checks.probe_video(rendered, deadline=deadline)
    return {
        "version": 1,
        "id": job["id"],
        "status": "ok",
        "source": origin,
        "origin": origin,
        "catalog_strong": True,
        "image_motion": {"aspect": job["aspect"], "motion": motion},
        "clip": {
            "clip_id": ref,
            "media_ref": ref,
            "source_url": source_url,
            "start": 0,
            "len": duration,
            "out_dur": duration,
            "fit": "image_motion",
            "score": 0.0,
            "sim": 0.0,
            "prob": 0.0,
            "crop": [0, 0, 1, 1],
            "src_w": sw,
            "src_h": sh,
            "out_w": rw,
            "out_h": rh,
            "phash": checks.mid_phash(rendered, deadline=deadline),
        },
        "used": "primary",
        "tried": [],
        "fallback": False,
        "needs_retry": False,
    }


def select_explicit(root, job, assignment):
    """Download, validate and normalize one explicitly chosen video."""
    root = Path(root)
    media = job["media"]
    ref = media["ref"]
    origin = job["source_kind"]
    deadline = time.monotonic() + 600
    record = None
    rendered = root / "clip.mp4"
    rendered.unlink(missing_ok=True)
    (root / "image-canvas.png").unlink(missing_ok=True)
    (root / "contribution" / "imported.json").unlink(missing_ok=True)
    reason = "No se pudo preparar el archivo del aporte."
    try:
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            if ref.startswith("asset:"):
                reason = "No se pudo preparar el SVG del repositorio."
                result = _select_image(root, job, ref, None, folder, deadline)
                _atomic_json(root / "result.json", result)
                return result
            if origin == "catalog_pinned":
                if "motion" in media or "framing" in media:
                    raise ExplicitMediaError("motion/framing solo para imagenes.")
                source = folder / "asset.mp4"
                reason = "No se pudo descargar o verificar el clip del catalogo."
                _download(assignment["primary"], source, None)
                source_url = assignment["primary"]["source_url"]
            else:
                reason = "Registro de aporte no accesible o invalido."
                record = registry_record(ref)
                if record.get("kind") == "image":
                    reason = "No se pudo descargar o preparar la imagen del aporte."
                    result = _select_image(root, job, ref, record, folder, deadline)
                    _atomic_json(root / "result.json", result)
                    return result
                if "motion" in media or "framing" in media:
                    raise ExplicitMediaError("motion/framing solo para imagenes.")
                source_url = record["url"]
                reason = "No se pudo descargar el video del aporte."
                source = asyncio.run(
                    download_media(
                        source_url,
                        folder,
                        max_bytes=100 * 1024 * 1024,
                        user_agent=_USER_AGENT,
                    )
                )
            reason = "No se pudo leer el video del aporte."
            if record is None:
                sw, sh, duration = checks.probe_video(source, deadline=deadline)
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("scene timeout")
                probe = probe_media(source, timeout=min(30, remaining))
                sw, sh = probe.display_width, probe.display_height
                duration = probe.duration_seconds
            if not 0 < duration <= 120.04:
                raise ExplicitMediaError("Duracion de aporte no valida.")
            width, height = framing.target_size(job["aspect"])
            if not framing.resolution_ok(sw, sh, width, height, job.get("min_short_side", 720)):
                raise ExplicitMediaError("Resolucion insuficiente del aporte.")
            start, end = window_bounds(media, duration, job["len_est"])
            span = min(end - start, job["len_fetch"])
            vf, crop = framing.crop_filter(sw, sh, job["aspect"])
            reason = "No se pudieron decodificar o normalizar los fotogramas del aporte."
            for at in _starts(
                source, sw, sh, duration, start, end, span, "start" in media, deadline
            ):
                reason = "No se pudo normalizar el video del aporte."
                _transcode(source, rendered, at, span, vf, deadline)
                reason = "No se pudo validar el MP4 generado para el aporte."
                rw, rh, out_duration = checks.probe_video(rendered, deadline=deadline)
                black, frozen = checks.black_frozen(rendered, deadline=deadline)
                if not black and not frozen and out_duration >= job["len_est"] / 1.25:
                    break
            else:
                raise ExplicitMediaError("Aporte negro, congelado o demasiado corto.")
            identifier = ref
            if record is not None:
                reason = "No se pudo preparar el indice del aporte."
                identifier = _index_manifest(
                    root, rendered, record, ref, at, out_duration, job["aspect"], deadline
                )
            reason = "No se pudo verificar el resultado del aporte."
            clip = {
                "clip_id": identifier,
                "media_ref": ref,
                "source_url": source_url,
                "start": at,
                "len": span,
                "out_dur": out_duration,
                "fit": "window",
                "score": 0.0,
                "sim": 0.0,
                "prob": 0.0,
                "crop": list(crop),
                "src_w": sw,
                "src_h": sh,
                "out_w": rw,
                "out_h": rh,
                "phash": checks.mid_phash(rendered, deadline=deadline),
            }
            result = {
                "version": 1,
                "id": job["id"],
                "status": "ok",
                "source": origin,
                "origin": origin,
                "catalog_strong": True,
                "clip": clip,
                "used": "primary",
                "tried": [],
                "fallback": False,
                "needs_retry": False,
            }
    except (ExplicitMediaError, image_motion.ImageMotionError) as exc:
        result = {"status": "fail", "reason": str(exc)}
    except httpx.HTTPStatusError as exc:
        # Never include exception text: it can contain a signed URL or token.
        result = {"status": "fail", "reason": f"{reason} HTTP {exc.response.status_code}."}
    except DownloadError as exc:
        # DownloadError also wraps external errors; expose only a whitelisted status.
        status = re.fullmatch(r"HTTP ([1-5][0-9]{2})", str(exc))
        detail = f" HTTP {status[1]}." if status else ""
        result = {"status": "fail", "reason": reason + detail}
    except (TimeoutError, httpx.TimeoutException):
        result = {"status": "fail", "reason": f"{reason} Tiempo de espera agotado."}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, httpx.HTTPError):
        result = {"status": "fail", "reason": reason}
    if result["status"] != "ok":
        rendered.unlink(missing_ok=True)
        (root / "image-canvas.png").unlink(missing_ok=True)
        (root / "contribution" / "imported.json").unlink(missing_ok=True)
        result.update(
            {
                "version": 1,
                "id": job["id"],
                "tried": [],
                "skip_import": job.get("on_error", "fail") != "search",
            }
        )
    _atomic_json(root / "result.json", result)
    return result
