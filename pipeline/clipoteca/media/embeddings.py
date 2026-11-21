"""M21: bounded CLIP scene semantics; no provider calls or eager model downloads.

Image model: clip-ViT-B-32; multilingual text model:
sentence-transformers/clip-ViT-B-32-multilingual-v1. Both output 512 dimensions.
Source is ALWAYS a local file already admitted by security/download.py or HLS.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import numpy as np
from PIL import Image

from ..security.download import MAX_DOWNLOAD_BYTES
from .probe import probe_media
from .process_env import safe_media_env
from .scenes import MAX_WINDOW_SECONDS

DIMENSION = 512
FRAME_WIDTH = FRAME_HEIGHT = 128
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
LONG_VIDEO_THRESHOLD_SECONDS = 180.0
SHORT_FPS = 1.0
LONG_FPS = 0.5
MAX_FRAMES_PER_VIDEO = 480
MAX_FFMPEG_OUTPUT = MAX_FRAMES_PER_VIDEO * FRAME_BYTES
SMOOTH_SECONDS = 3.0
MAX_CLIP_SECONDS = 20.0
SOURCE_PATTERN = re.compile(r"^clip-[0-9]{6}$")


class EmbeddingError(RuntimeError):
    """Safe-to-report semantic extraction failure (never includes remote URLs)."""


class EmbeddingUnavailable(EmbeddingError):
    """Optional models are not installed or available on this runner."""


@runtime_checkable
class Encoder(Protocol):
    """Injectable model boundary; tests use a deterministic fake and no network."""

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray: ...

    def encode_text(self, text: str) -> np.ndarray: ...


class CLIPEncoder:
    """Lazy optional sentence-transformers models with matching 512-D spaces."""

    def __init__(self, *, device: str = "cpu") -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable("embeddings_extra_not_installed") from exc
        if device not in ("cpu", "cuda"):
            raise ValueError("device must be cpu or cuda")
        if device == "cuda" and not torch.cuda.is_available():
            raise EmbeddingUnavailable("cuda_not_available")
        try:
            # Image CLIP provides the target space; multilingual text aligns to it.
            self.image_model = SentenceTransformer("clip-ViT-B-32", device=device)
            self.text_model = SentenceTransformer(
                "sentence-transformers/clip-ViT-B-32-multilingual-v1", device=device
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise EmbeddingUnavailable("model_weights_unavailable") from exc
        self.device = device

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        return np.asarray(
            self.image_model.encode(list(images), convert_to_numpy=True, show_progress_bar=False),
            dtype=np.float32,
        )

    def encode_text(self, text: str) -> np.ndarray:
        return np.asarray(
            self.text_model.encode([text], convert_to_numpy=True, show_progress_bar=False)[0],
            dtype=np.float32,
        )


@lru_cache(maxsize=2)
def load_encoder(*, device: str = "cpu") -> CLIPEncoder:
    """Model access is opt-in via installed extra and cached per process."""
    return CLIPEncoder(device=device)


def normalize_vector(vector: np.ndarray | Sequence[float]) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    if value.shape != (DIMENSION,) or not np.all(np.isfinite(value)):
        raise EmbeddingError("embedding_vector_invalid")
    norm = float(np.linalg.norm(value))
    if norm < 1e-8:
        raise EmbeddingError("embedding_zero_norm")
    return value / norm


def normalize_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float32)
    if rows.ndim != 2 or rows.shape[1] != DIMENSION or not np.all(np.isfinite(rows)):
        raise EmbeddingError("frame_embeddings_invalid")
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    if np.any(norms < 1e-8):
        raise EmbeddingError("frame_embedding_zero_norm")
    return rows / norms


def sampling_fps(duration_seconds: float) -> float:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration must be positive and finite")
    return SHORT_FPS if duration_seconds <= LONG_VIDEO_THRESHOLD_SECONDS else LONG_FPS


@dataclass(frozen=True)
class FrameEmbeddings:
    times: np.ndarray
    vectors: np.ndarray

    def __post_init__(self) -> None:
        times = np.asarray(self.times, dtype=np.float32)
        vectors = normalize_rows(self.vectors)
        if (
            times.ndim != 1
            or len(times) != len(vectors)
            or not len(times)
            or not np.all(np.isfinite(times))
            or np.any(np.diff(times) <= 0)
        ):
            raise EmbeddingError("frame_timeline_invalid")
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "vectors", vectors)


def extract_frame_embeddings(
    source: str | Path,
    encoder: Encoder,
    *,
    timeout: float = 150,
    runner=None,
) -> FrameEmbeddings:
    """Decode bounded RGB frames only from a previously secured LOCAL video.

    No arbitrary HTTP URL/FFmpeg remote protocol is permitted. Local file
    was downloaded through existing SSRF, peer-IP pinning and byte limits.
    """
    path = Path(source)
    if not path.is_file() or path.is_symlink():
        raise EmbeddingError("embedding_local_file_required")
    size = path.stat().st_size
    if not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise EmbeddingError("embedding_input_size_limit")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    horizon = min(probe_media(path, timeout=min(timeout, 30)).duration_seconds, MAX_WINDOW_SECONDS)
    fps = sampling_fps(horizon)
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(path),
        "-t",
        f"{horizon:.3f}",
        "-an",
        "-vf",
        (
            f"fps={fps},scale={FRAME_WIDTH}:{FRAME_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={FRAME_WIDTH}:{FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-frames:v",
        str(MAX_FRAMES_PER_VIDEO),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    try:
        result = (runner or subprocess.run)(
            command, capture_output=True, check=True, timeout=timeout, env=safe_media_env()
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as exc:
        raise EmbeddingError("embedding_ffmpeg_unavailable") from exc
    data = result.stdout
    if (
        not data
        or len(data) > MAX_FFMPEG_OUTPUT
        or len(data) % FRAME_BYTES
        or len(data) // FRAME_BYTES > MAX_FRAMES_PER_VIDEO
    ):
        raise EmbeddingError("embedding_frame_limit_or_truncation")
    count = len(data) // FRAME_BYTES
    rows = []
    for index in range(0, count, 16):
        images = [
            Image.frombytes(
                "RGB",
                (FRAME_WIDTH, FRAME_HEIGHT),
                data[j * FRAME_BYTES : (j + 1) * FRAME_BYTES],
            )
            for j in range(index, min(index + 16, count))
        ]
        batch = np.asarray(encoder.encode_images(images), dtype=np.float32)
        if batch.shape != (len(images), DIMENSION):
            raise EmbeddingError("embedding_image_dimension_mismatch")
        rows.append(batch)
    timeline = np.arange(count, dtype=np.float32) / fps
    return FrameEmbeddings(timeline, np.concatenate(rows, axis=0))


def moving_average(times: np.ndarray, scores: np.ndarray, *, seconds: float = SMOOTH_SECONDS):
    """3-second centred time-weighted mean of sampled cosine scores."""
    times = np.asarray(times, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    if times.ndim != 1 or scores.shape != times.shape or seconds <= 0:
        raise ValueError("invalid moving-average timeline")
    radius = seconds / 2
    return np.asarray(
        [float(np.mean(scores[np.abs(times - t) <= radius + 1e-7])) for t in times],
        dtype=np.float32,
    )


def cosine_scores(samples: FrameEmbeddings, encoder: Encoder, query: str) -> np.ndarray:
    query_vector = normalize_vector(encoder.encode_text(query))
    return np.clip(samples.vectors @ query_vector, -1.0, 1.0)


def window_similarity(
    samples: FrameEmbeddings,
    similarities: np.ndarray,
    *,
    start: float,
    end: float,
    smooth_seconds: float = SMOOTH_SECONDS,
) -> float | None:
    if not (0 <= start < end <= MAX_WINDOW_SECONDS + 1e-4) or end - start > MAX_CLIP_SECONDS + 1e-4:
        raise ValueError("semantic window must be within 20 seconds")
    mask = (samples.times >= start) & (samples.times < end)
    if not np.any(mask):
        return None
    # Do not bleed context across the validated scene boundary: titles or
    # unrelated adjacent scenes must not contaminate this window's average.
    smoothed = moving_average(
        samples.times[mask], np.asarray(similarities)[mask], seconds=smooth_seconds
    )
    return float(np.mean(smoothed))


def clip_vector(samples: FrameEmbeddings, *, start: float, end: float) -> np.ndarray:
    """L2-normalized mean image vector for M22's brute-force reader."""
    if not 0 <= start < end or end - start > MAX_CLIP_SECONDS + 1e-4:
        raise ValueError("clip vector window must be within 20 seconds")
    mask = (samples.times >= start) & (samples.times < end)
    if not np.any(mask):
        raise EmbeddingError("clip_embedding_frames_missing")
    return normalize_vector(np.mean(samples.vectors[mask], axis=0))
