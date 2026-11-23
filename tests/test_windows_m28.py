"""M28 robust CLIP window ranking with synthetic normalized embeddings."""

from __future__ import annotations

import math

import numpy as np
import pytest

from clipoteca.studio.windows import choose_window, fit_mode, windows


def _array() -> tuple[dict, np.ndarray]:
    ts = np.arange(0, 6, 0.5, dtype=np.float32)
    sim = np.array([0.3] * 4 + [0.8] * 4 + [0.4] * 4, dtype=np.float32)
    emb = np.zeros((len(ts), 512), dtype=np.float32)
    emb[:, 0] = sim
    emb[:, 1] = np.sqrt(1 - sim**2)
    query = np.zeros(512, dtype=np.float32)
    query[0] = 1
    return {
        "emb": emb,
        "ts": ts,
        "dur": 6.0,
        "sharp": np.full(len(ts), 100.0),
        "bright": np.full(len(ts), 120.0),
        "shift": np.zeros(len(ts)),
        "diff": np.full(len(ts), 5.0),
        "cuts": [],
    }, query


def test_relevant_window_wins_and_exact_reference_score() -> None:
    data, prompt = _array()
    ranked = windows(data, prompt, 2.0)
    assert ranked[0]["start"] == 1.5  # same robust score, earlier start wins
    assert ranked[0]["sim"] == 0.8
    assert ranked[0]["prob"] == 1
    assert ranked[0]["score"] == pytest.approx(8.47)
    assert ranked[0]["sharp"] == pytest.approx(
        round((math.log10(101) - 1.3) / 1.2, 2),
    )
    picked, reason = choose_window(ranked, 0.22, 0.5)
    assert picked == ranked[0]
    assert reason == "ok"


def test_penalty_for_internal_cut_and_darkness() -> None:
    data, prompt = _array()
    baseline = windows(data, prompt, 2, start=2)[0]["score"]
    data["cuts"] = [2.9]
    cut = windows(data, prompt, 2, start=2)[0]
    assert cut["cuts"] == 1
    assert cut["score"] == pytest.approx(baseline - 0.6, abs=0.001)
    data["cuts"] = []
    data["bright"][4:8] = 20
    dark = windows(data, prompt, 2, start=2)[0]
    assert dark["score"] < baseline - 1


def test_penalty_for_static_video_and_jerk() -> None:
    data, prompt = _array()
    baseline = windows(data, prompt, 2, start=2)[0]["score"]
    data["diff"][4:8] = 0.05
    still = windows(data, prompt, 2, start=2)[0]
    assert still["static"]
    assert still["score"] == pytest.approx(baseline - 0.6, abs=0.001)
    data["diff"][:] = 5
    data["shift"][4:8] = [0, 12, 0, 12]
    shaky = windows(data, prompt, 2, start=2)[0]
    assert shaky["jerk"] > 4
    assert shaky["score"] < baseline


def test_few_frames_are_rejected() -> None:
    data, prompt = _array()
    data["ts"] = np.full(12, 20.0)
    assert windows(data, prompt, 2) == []


def test_fit_modes_and_whole_clip_window() -> None:
    assert fit_mode(3, 2) == "window"
    assert fit_mode(1.5, 2) == "whole"
    assert fit_mode(0.84, 2) == "whole"
    assert fit_mode(0.83, 2) is None
    data, prompt = _array()
    for key in ("emb", "ts", "sharp", "bright", "shift", "diff"):
        data[key] = data[key][:2]
    data["dur"] = 1.0
    result = windows(data, prompt, 2.0)
    assert len(result) == 1
    assert result[0]["start"] == 0


def test_threshold_reasons_and_stable_ties() -> None:
    assert choose_window([], 0.22, 0.5) == (None, "no_window")
    assert choose_window(
        [{"sim": 0.21, "prob": 0.99, "start": 0, "score": 10}],
        0.22,
        0.5,
    ) == (None, "low_sim")
    assert choose_window(
        [{"sim": 0.7, "prob": 0.2, "start": 0, "score": 10}],
        0.22,
        0.5,
    ) == (None, "low_prob")
    options = [
        {"sim": 0.3, "prob": 0.9, "start": 1.0, "score": 5},
        {"sim": 0.3, "prob": 0.9, "start": 0.0, "score": 5},
    ]
    picked, reason = choose_window(options, 0.22, 0.5)
    assert picked == options[1] and reason == "ok"


def test_negative_embedding_softmax_lowers_probability() -> None:
    data, prompt = _array()
    other = np.zeros((1, 512), dtype=np.float32)
    other[0, 0] = 1.0
    scored = windows(data, prompt, 2, start=2, nemb=other)
    assert scored[0]["prob"] == pytest.approx(0.5, abs=0.001)
