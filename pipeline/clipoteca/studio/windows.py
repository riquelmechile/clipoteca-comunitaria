"""Score temporally stable CLIP windows from precomputed, offline frame arrays."""

from __future__ import annotations

import numpy as np


def fit_mode(duration: float, length: float) -> str | None:
    """Choose normal trim, bounded stretch, or reject too-short material."""
    if not np.isfinite(duration) or not np.isfinite(length) or length <= 0:
        return None
    if duration >= length:
        return "window"
    if duration >= 0.42 * length:
        return "whole"
    return None


def windows(
    A: dict,
    pemb: np.ndarray,
    L: float,
    start: float | None = None,
    nemb: np.ndarray | None = None,
) -> list[dict]:
    """Rank candidate start times in 0.5 s steps by relevance and temporal quality."""
    mode = fit_mode(float(A["dur"]), L)
    if mode is None:
        return []
    span = float(A["dur"]) if mode == "whole" else L
    embedding = np.asarray(A["emb"], dtype=np.float32)
    prompt = np.asarray(pemb, dtype=np.float32)
    sims = embedding @ prompt
    output: list[dict] = []
    if nemb is not None and len(nemb):
        distractors = np.asarray(nemb, dtype=np.float32)
        logits = 100 * np.concatenate(
            [sims[:, None], embedding @ distractors.T],
            axis=1,
        )
        logits -= logits.max(axis=1, keepdims=True)
        exponentials = np.exp(logits)
        probabilities = exponentials[:, 0] / exponentials.sum(axis=1)
    else:
        probabilities = np.ones_like(sims)
    if mode == "whole":
        starts = [0.0]
    elif start is not None:
        starts = [float(start)]
    else:
        max_start = max(0.0, float(A["dur"]) - span - 0.05)
        starts = np.arange(0.0, max_start + 1e-6, 0.5)
    for raw_start in starts:
        s = float(raw_start)
        idx = np.where((A["ts"] >= s) & (A["ts"] < s + span))[0]
        if len(idx) < max(2, int(span * 2 * 0.8)):
            continue
        cuts = sum(1 for c in A["cuts"] if s + 0.15 < c < s + span - 0.15)
        quartile = len(idx) // 4
        sim = float(np.mean(np.sort(sims[idx])[quartile:]))
        probability = float(np.mean(np.sort(probabilities[idx])[quartile:]))
        sharp = float(np.clip((np.log10(np.median(A["sharp"][idx]) + 1) - 1.3) / 1.2, 0, 1))
        bright = float(np.mean(A["bright"][idx]))
        dark = max(0, (45 - bright) / 45 * 2) + max(0, (bright - 235) / 20)
        jerk = float(np.std(np.diff(A["shift"][idx]))) if len(idx) > 2 else 0.0
        shake = 0.08 * max(0, jerk - 4)
        still = float(np.median(A["diff"][idx][1:])) < 0.6
        static = 0.6 if still else 0.0
        score = 10 * (sim - 0.2) + 2 * probability + 0.8 * sharp
        score -= dark + shake + static + 0.6 * cuts
        output.append(
            {
                "start": round(s, 2),
                "sim": round(sim, 4),
                "prob": round(probability, 3),
                "sharp": round(sharp, 2),
                "bright": round(bright, 1),
                "jerk": round(jerk, 2),
                "static": bool(still),
                "cuts": cuts,
                "score": round(score, 3),
                "idx": idx,
            }
        )
    return sorted(output, key=lambda item: (-item["score"], item["start"]))


def choose_window(
    wins: list[dict],
    min_sim: float = 0.22,
    min_prob: float = 0.5,
) -> tuple[dict | None, str]:
    """Return the best valid window and the reason if none qualifies."""
    if not wins:
        return None, "no_window"
    ordered = sorted(wins, key=lambda item: (-item["score"], item["start"]))
    viable = [item for item in ordered if item["sim"] >= min_sim]
    if not viable:
        return None, "low_sim"
    for item in viable:
        if item["prob"] >= min_prob:
            return item, "ok"
    return None, "low_prob"
