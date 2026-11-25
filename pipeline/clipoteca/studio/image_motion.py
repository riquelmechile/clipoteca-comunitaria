"""Render a raster photo or trusted repository SVG as an animated video."""

from __future__ import annotations

import io
import math
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from ..media.process_env import safe_media_env
from . import checks, framing
from .repo_assets import asset_path

MAX_PIXELS = 24_000_000
MAX_BYTES = 10 * 1024 * 1024
MOTIONS = ("auto", "zoom_in", "zoom_out", "pan_left", "pan_right")
FRAMINGS = ("cover", "contain")
PALETTE = (11, 23, 49)


class ImageMotionError(ValueError):
    """Safe image-specific diagnostic."""


def load_photo(path: Path) -> Image.Image:
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ImageMotionError("Imagen supera 10 MiB.")
        with Image.open(path, formats=["JPEG", "PNG", "WEBP"]) as raw:
            if raw.width * raw.height > MAX_PIXELS:
                raise ImageMotionError("Imagen supera 24 megapixeles.")
            if getattr(raw, "n_frames", 1) != 1:
                raise ImageMotionError("Se requiere una imagen estatica.")
            # Copy only pixels; never persist EXIF, ICC, or embedded comments.
            rotated = ImageOps.exif_transpose(raw).convert("RGBA")
            clean = Image.new("RGBA", rotated.size)
            clean.paste(rotated)
            return clean
    except ImageMotionError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageMotionError("No se pudo leer la imagen JPEG, PNG o WebP.") from exc


def rasterize_asset(ref: str) -> Image.Image:
    path = asset_path(ref)
    try:
        # Stdin has no base URI; librsvg cannot resolve external resource paths.
        # The only accepted input is a trusted, checked-in SVG.
        with tempfile.TemporaryDirectory() as folder:
            process = subprocess.run(
                [
                    "rsvg-convert", "--format=png", "--keep-aspect-ratio",
                    "--width=3840", "--height=3840",
                ],
                input=path.read_bytes(),
                capture_output=True,
                check=True,
                timeout=30,
                cwd=folder,
                env=safe_media_env(),
            )
        with Image.open(io.BytesIO(process.stdout), formats=["PNG"]) as raw:
            return raw.convert("RGBA")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ImageMotionError("No se pudo rasterizar el SVG del repositorio.") from exc


def prepare_canvas(
    image: Image.Image, canvas: Path, aspect: str, mode: str, *, repo_asset: bool = False
) -> tuple[int, int]:
    width, height = framing.target_size(aspect)
    sw, sh = image.size
    if max(sw, sh) < 0.5 * max(width, height):
        raise ImageMotionError("Resolucion insuficiente: lado largo menor al 50% del formato.")
    if mode not in FRAMINGS:
        raise ImageMotionError("Framing no valido.")
    # Supersampled coordinates reduce integer rounding in FFmpeg zoompan.
    size = (width * 3, height * 3)
    rgba = image.convert("RGBA")
    opaque = Image.new("RGB", rgba.size, PALETTE)
    opaque.paste(rgba, mask=rgba.getchannel("A"))
    if mode == "cover":
        base = ImageOps.fit(opaque, size, method=Image.Resampling.LANCZOS)
    else:
        if repo_asset:
            base = Image.new("RGB", size, PALETTE)
        else:
            background = ImageOps.fit(opaque, size, method=Image.Resampling.LANCZOS)
            base = background.filter(ImageFilter.GaussianBlur(max(size) / 65))
        # Reserve motion-safe margins: the diagram remains inside the viewport.
        margin = 0.70 if repo_asset else 0.78
        foreground = ImageOps.contain(
            rgba, tuple(round(v * margin) for v in size), Image.Resampling.LANCZOS
        )
        base.paste(
            foreground,
            ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2),
            foreground.getchannel("A"),
        )
    base.save(canvas, format="PNG")
    return sw, sh


def render_motion(
    canvas: Path, destination: Path, aspect: str, motion: str, seconds: float, *,
    repo_asset: bool = False,
) -> None:
    if motion not in MOTIONS:
        raise ImageMotionError("Motion no valido.")
    if not math.isfinite(seconds) or not 0 < seconds <= 75:
        raise ImageMotionError("Duracion de imagen no valida.")
    width, height = framing.target_size(aspect)
    frames = math.ceil(seconds * 30)
    progress = f"on/{max(1, frames - 1)}"
    strength = 0.24 if repo_asset else 0.42
    motion = "zoom_in" if motion == "auto" else motion
    if motion == "zoom_in":
        zoom = f"1+{strength}*{progress}"
    elif motion == "zoom_out":
        zoom = f"1+{strength}*(1-{progress})"
    else:
        zoom = str(1 + strength)
    x = "(iw-iw/zoom)/2"
    if motion in ("pan_left", "pan_right"):
        direction = progress if motion == "pan_right" else f"(1-{progress})"
        x = f"(iw-iw/zoom)*(0.25+0.5*{direction})"
    vf = (
        "format=yuv444p,"
        f"zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':"
        f"d={frames}:s={width}x{height}:fps=30,setsar=1,format=yuv420p"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-filter_threads", "2", "-i", str(canvas),
                "-vf", vf, "-frames:v", str(frames), "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "19", "-threads", "2",
                "-movflags", "+faststart", str(destination),
            ],
            capture_output=True, check=True, timeout=240, env=safe_media_env(),
        )
        w, h, duration = checks.probe_video(destination)
        black, frozen = checks.black_frozen(destination)
        if (w, h) != (width, height) or abs(duration - frames / 30) > 0.04:
            raise ImageMotionError("MP4 de imagen con geometria o duracion invalida.")
        if black or frozen:
            raise ImageMotionError("Imagen animada negra o congelada.")
    except ImageMotionError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, subprocess.SubprocessError, checks.CheckError) as exc:
        destination.unlink(missing_ok=True)
        raise ImageMotionError("No se pudo animar o validar la imagen.") from exc
