"""Local CLIP ViT-B/32 ONNX scoring and video-frame metrics without downloads."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import numpy as np

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

MODEL_FILES = (
    "vision_model_quantized.onnx",
    "text_model_quantized.onnx",
    "tokenizer.json",
)


class ClipScoreError(RuntimeError):
    """The caller must provide complete local CLIP model artifacts."""


class ClipScorer:
    """Thread-safe lazy CPU sessions; test callers may inject fake sessions."""

    def __init__(
        self,
        model_dir: str | Path,
        sessions: dict[str, Any] | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self._sessions = sessions
        self._lock = threading.Lock()
        if sessions is None:
            missing = [name for name in MODEL_FILES if not (self.model_dir / name).is_file()]
            if missing:
                raise ClipScoreError("missing local CLIP assets: " + ", ".join(missing))

    def _sess(self) -> dict[str, Any]:
        if self._sessions is not None:
            return self._sessions
        with self._lock:
            if self._sessions is not None:
                return self._sessions
            import onnxruntime as ort
            from tokenizers import Tokenizer

            threads = int(
                os.environ.get("CLIPOTECA_ORT_THREADS", max(2, (os.cpu_count() or 2) // 2))
            )
            if threads <= 0:
                raise ClipScoreError("CLIPOTECA_ORT_THREADS must be positive")
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            self._sessions = {
                "v": ort.InferenceSession(
                    str(self.model_dir / MODEL_FILES[0]),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                ),
                "t": ort.InferenceSession(
                    str(self.model_dir / MODEL_FILES[1]),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                ),
                "tok": Tokenizer.from_file(str(self.model_dir / MODEL_FILES[2])),
            }
            return self._sessions

    def text_emb(self, texts: list[str]) -> np.ndarray:
        """Normalize one 512-wide embedding per tokenized, lowercased prompt."""
        if not texts:
            return np.zeros((0, 512), dtype=np.float32)
        sessions = self._sess()
        output = []
        for text in texts:
            ids = sessions["tok"].encode(text.lower()).ids[:77]
            encoded = np.array([ids], dtype=np.int64)
            value = np.asarray(
                sessions["t"].run(None, {"input_ids": encoded})[0][0],
                dtype=np.float32,
            )
            output.append(value / max(float(np.linalg.norm(value)), 1e-12))
        return np.asarray(output, dtype=np.float32)

    @staticmethod
    def _prep(bgr: np.ndarray) -> np.ndarray:
        """Central 224x224 crop after shortest-side bicubic resize and CLIP RGB norm."""
        import cv2

        h, w = bgr.shape[:2]
        if h <= 0 or w <= 0:
            raise ClipScoreError("frame must have nonzero dimensions")
        scale = 224 / min(h, w)
        resized = cv2.resize(
            bgr,
            (max(224, round(w * scale)), max(224, round(h * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
        top = (resized.shape[0] - 224) // 2
        left = (resized.shape[1] - 224) // 2
        rgb = resized[top : top + 224, left : left + 224, ::-1].astype(np.float32) / 255
        return ((rgb - MEAN) / STD).transpose(2, 0, 1)

    def image_emb(self, frames: list[np.ndarray], bs: int = 32) -> np.ndarray:
        """Embed frames in batches of up to 32 without network or global sessions."""
        if not frames:
            return np.zeros((0, 512), dtype=np.float32)
        if bs <= 0:
            raise ClipScoreError("batch size must be positive")
        sessions = self._sess()
        chunks = []
        for i in range(0, len(frames), bs):
            pixels = np.stack([self._prep(frame) for frame in frames[i : i + bs]])
            values = np.asarray(
                sessions["v"].run(None, {"pixel_values": pixels})[0],
                dtype=np.float32,
            )
            norm = np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)
            chunks.append(values / norm)
        return np.concatenate(chunks)

    @staticmethod
    def frame_metrics(
        frames: list[np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Sharpness, brightness, phase shift and mean abs change for each frame."""
        import cv2

        sharp: list[float] = []
        bright: list[float] = []
        shift = [0.0]
        diff = [0.0]
        previous = None
        for frame in frames:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharp.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            bright.append(float(gray.mean()))
            floating = np.float32(gray)
            if previous is not None:
                (dx, dy), _ = cv2.phaseCorrelate(previous, floating)
                shift.append(float(np.hypot(dx, dy)))
                diff.append(float(np.abs(floating - previous).mean()))
            previous = floating
        if not frames:
            return (np.array([]), np.array([]), np.array([]), np.array([]))
        return (
            np.asarray(sharp),
            np.asarray(bright),
            np.asarray(shift[: len(frames)]),
            np.asarray(diff[: len(frames)]),
        )


_prep = ClipScorer._prep
frame_metrics = ClipScorer.frame_metrics
