"""M28 offline catalog matching, fair assignment and injected CLIP sessions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from clipoteca.place_names import query_names
from clipoteca.studio.clipscore import ClipScoreError, ClipScorer, MEAN, STD
from clipoteca.studio.library import (
    assign_scenes,
    candidates,
    load_catalog,
    sufficient_query_match,
    text_score,
)


FIXTURE = Path(__file__).parent / "fixtures/m28/catalogo.json"


def _index():
    return load_catalog(FIXTURE)


def _job(es: str, en: str, *, length: float = 10, avoid: list[str] | None = None):
    return {
        "id": "scene",
        "len_est": length,
        "min_short_side": 720,
        "queries": {"es": [es], "en": [en]},
        "avoid": avoid or [],
    }


def test_catalog_filters_and_counts_bad_entries() -> None:
    index = _index()
    assert len(index["clips"]) == 13
    assert index["invalid"] == 1
    assert index["unpublished"] == 1
    assert [c["id"] for c in index["clips"]] == sorted(c["id"] for c in index["clips"])
    assert all(c["status"] == "published" and c["storage"]["asset_url"] for c in index["clips"])


def test_text_score_bilingual_accents_whole_words_and_avoid() -> None:
    clip = next(c for c in _index()["clips"] if c["id"] == "clip-100004")
    points, matched = text_score(clip, ["cascada rocas"], ["waterfall rocks"], [])
    assert points == 2.0
    assert matched == ["cascada", "rocas", "rocks", "waterfall"]
    result, _ = text_score(clip, ["cascadas"], [], [])
    assert result == 0  # no substring or plural stemming
    bad, words = text_score(clip, ["cascada"], [], ["RÍO CON CASCADA"])
    assert bad == -5.5
    assert words == ["cascada"]


def test_candidates_duration_resolution_order_fit_and_urls() -> None:
    job = _job("lago", "lake", length=10)
    matches = candidates(job, _index())
    ids = [entry["clip_id"] for entry in matches]
    assert "clip-100001" in ids and "clip-100002" in ids
    assert "clip-100009" not in ids  # width is too small
    assert "clip-100012" not in ids  # shorter than 0.42 times target
    assert "clip-100010" not in ids  # draft
    assert [(-e["score"], e["clip_id"]) for e in matches] == sorted(
        (-e["score"], e["clip_id"]) for e in matches
    )
    assert all(e["fit"] in ("window", "whole") for e in matches)
    assert all(e["asset_url"].startswith("https://example.invalid/") for e in matches)
    assert all(e["source_url"].startswith("https://example.invalid/") for e in matches)


def test_candidates_avoid_excludes_unwanted_context() -> None:
    job = _job("bosque", "forest", avoid=["interior"])
    ids = [entry["clip_id"] for entry in candidates(job, _index())]
    assert "clip-100003" in ids
    assert "clip-100014" in ids
    assert "clip-100011" not in ids


def test_whole_fit_for_partially_short_clip() -> None:
    job = _job("lago", "lake", length=25)
    matches = candidates(job, _index())
    one = next(c for c in matches if c["clip_id"] == "clip-100002")
    assert one["duration"] == 15
    assert one["fit"] == "whole"


def test_assign_scenes_rarer_first_and_no_primary_duplicates() -> None:
    general = _job("lago", "lake")
    general["id"] = "general"
    rare = _job("turquesa", "turquoise")
    rare["id"] = "rare"
    third = _job("bosque", "forest")
    third["id"] = "forest"
    jobs = [general, rare, third]
    answers = assign_scenes(jobs, _index(), top_n=4)
    assert [a["id"] for a in answers] == ["general", "rare", "forest"]
    assert all(a["status"] == "catalog" for a in answers)
    assert answers[1]["primary"]["clip_id"] == "clip-100001"
    primaries = [a["primary"]["clip_id"] for a in answers]
    assert len(set(primaries)) == 3
    assert all(
        len(a["backups"]) <= 3 and all(b["clip_id"] not in primaries for b in a["backups"])
        for a in answers
    )
    assert assign_scenes(jobs, _index(), top_n=4) == answers


def test_no_text_match_uses_catalog_fallback_without_network() -> None:
    impossible = _job("elefantes cósmicos", "cosmic elephants")
    impossible["id"] = "missing"
    assignment = assign_scenes([impossible], _index())[0]
    assert assignment["id"] == "missing"
    assert assignment["status"] == "catalog"
    assert assignment["primary"]["fallback"] is True


def test_clipscore_requires_only_local_files(tmp_path: Path) -> None:
    with pytest.raises(ClipScoreError, match="vision_model_quantized.onnx"):
        ClipScorer(tmp_path)


def test_clipscore_text_and_image_fake_sessions(tmp_path: Path) -> None:
    class FakeTokenizer:
        calls: list[str] = []

        def encode(self, text: str):
            self.calls.append(text)
            return SimpleNamespace(ids=list(range(99)))

    class FakeSession:
        def __init__(self, mode: str):
            self.mode = mode
            self.calls = []

        def run(self, _outputs, feeds):
            self.calls.append(feeds)
            n = next(iter(feeds.values())).shape[0]
            data = np.zeros((n, 512), dtype=np.float32)
            data[:, 0], data[:, 1] = 3, 4
            return [data]

    tokenizer = FakeTokenizer()
    texts = FakeSession("text")
    visual = FakeSession("vision")
    scorer = ClipScorer(tmp_path, sessions={"tok": tokenizer, "t": texts, "v": visual})
    prompt_embeddings = scorer.text_emb(["HOLA", "Cascada"])
    assert tokenizer.calls == ["hola", "cascada"]
    assert prompt_embeddings.shape == (2, 512)
    assert np.allclose(prompt_embeddings[:, :2], [[0.6, 0.8], [0.6, 0.8]])
    assert all(x["input_ids"].shape == (1, 77) for x in texts.calls)
    assert scorer.image_emb([]).shape == (0, 512)
    # _prep uses cv2 only when frames are provided, never at module import time.
    pytest.importorskip("cv2")
    frames = [np.zeros((256, 256, 3), dtype=np.uint8) for _ in range(3)]
    frame_embeddings = scorer.image_emb(frames, bs=2)
    assert frame_embeddings.shape == (3, 512)
    assert np.allclose(frame_embeddings[:, :2], [[0.6, 0.8]] * 3)
    assert [c["pixel_values"].shape for c in visual.calls] == [
        (2, 3, 224, 224),
        (1, 3, 224, 224),
    ]


def test_prep_central_rgb_crop_normalization() -> None:
    pytest.importorskip("cv2")
    bgr = np.zeros((224, 448, 3), dtype=np.uint8)
    bgr[:, :112] = (0, 0, 255)
    bgr[:, 112:336] = (0, 255, 0)
    bgr[:, 336:] = (255, 0, 0)
    result = ClipScorer._prep(bgr)
    assert result.shape == (3, 224, 224)
    expected = (np.array([0, 1, 0], dtype=np.float32) - MEAN) / STD
    assert result[:, 100, 100] == pytest.approx(expected, abs=1e-5)


def test_frame_metrics_on_synthetic_shifted_checkerboard() -> None:
    pytest.importorskip("cv2")
    checker = (np.indices((128, 128)).sum(axis=0) % 2 * 255).astype(np.uint8)
    frame1 = np.repeat(checker[:, :, None], 3, axis=2)
    frame2 = np.roll(frame1, 8, axis=1)
    sharp, bright, shift, diff = ClipScorer.frame_metrics([frame1, frame2])
    assert all(len(x) == 2 for x in (sharp, bright, shift, diff))
    assert sharp.min() > 0
    assert np.allclose(bright[0], bright[1])
    assert shift[0] == 0 and diff[0] == 0
    assert np.isfinite(shift[1]) and np.isfinite(diff[1])
    assert ClipScorer.frame_metrics([])[0].size == 0


def test_duplicate_clip_id_keeps_first_and_counts_invalid(tmp_path: Path) -> None:
    import json

    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    original = next(c for c in raw if c.get("id") == "clip-100001")
    duplicate = json.loads(json.dumps(original))
    duplicate["storage"]["asset_url"] = "https://example.invalid/modified.mp4"
    raw.append(duplicate)
    destination = tmp_path / "duplicates.json"
    destination.write_text(json.dumps(raw))
    indexed = load_catalog(destination)
    assert indexed["invalid"] == 2
    assert indexed["unpublished"] == 1
    assert len(indexed["clips"]) == 13
    clips = [c for c in indexed["clips"] if c["id"] == "clip-100001"]
    assert len(clips) == 1
    assert clips[0]["storage"]["asset_url"] == original["storage"]["asset_url"]
    ranked = candidates(_job("lago", "lake"), indexed)
    assert len({c["clip_id"] for c in ranked}) == len(ranked)
    assert all(isinstance(c["size_bytes"], int) and len(c["sha256"]) == 64 for c in ranked)


def test_long_clip_and_high_fps_allowed_without_unused_media_fields(tmp_path: Path) -> None:
    import json

    original = next(c for c in _index()["clips"] if c["id"] == "clip-100001")
    simplified = {
        "id": "clip-25s",
        "status": "published",
        "storage": {"asset_url": "https://example.invalid/long.mp4"},
        "media": {
            "duration_seconds": 25.0,
            "width": 1920,
            "height": 1080,
            "size_bytes": 1024,
            "sha256": "a" * 64,
            "fps": 120,
        },
        "title": original["title"],
        "description": original["description"],
        "tags": original["tags"],
        "source": original["source"],
    }
    path = tmp_path / "simplified.json"
    path.write_text(json.dumps([simplified]))
    catalog = load_catalog(path)
    assert catalog["invalid"] == 0 and len(catalog["clips"]) == 1
    selected = candidates(_job("lago", "lake", length=10), catalog)
    assert len(selected) == 1
    assert selected[0]["clip_id"] == "clip-25s"
    assert selected[0]["duration"] == 25
    assert selected[0]["size_bytes"] == 1024
    assert selected[0]["sha256"] == "a" * 64


def test_candidate_resolution_exception_for_same_orientation(tmp_path: Path) -> None:
    import json

    original = next(c for c in _index()["clips"] if c["id"] == "clip-100001")
    other = json.loads(json.dumps(original))
    other["id"] = "clip-narrow"
    other["media"]["width"] = 1200
    other["media"]["height"] = 700
    path = tmp_path / "orientation.json"
    path.write_text(json.dumps([other]))
    catalog = load_catalog(path)
    horizontal = _job("lago", "lake")
    horizontal["aspect"] = "16:9"
    assert len(candidates(horizontal, catalog)) == 1
    vertical = {**horizontal, "aspect": "9:16"}
    assert candidates(vertical, catalog) == []


REAL_CATALOG = Path(__file__).parents[1] / "data" / "clips.json"


def test_known_chile_places_are_separate_entities() -> None:
    assert query_names(["Santiago Chile city"]) == {"santiago", "chile"}
    assert query_names(["Santiago Chile skyline Andes"]) == {"santiago", "chile", "andes"}
    assert "grey glacier" not in query_names(["Grey"])
    for query in (
        "Valparaíso",
        "Torres del Paine",
        "Chiloé",
        "Atacama",
        "Easter Island moai",
    ):
        assert query_names([query])


def test_real_catalog_chile_queries_keep_candidates() -> None:
    catalog = load_catalog(REAL_CATALOG)
    queries = (
        "Santiago Chile city",
        "Valparaíso",
        "Torres del Paine",
        "Chiloé",
        "Atacama",
        "Easter Island moai",
    )
    ranked = {query: candidates(_job(query, query), catalog) for query in queries}
    assert all(ranked[query] for query in queries)
    assert "clip-000118" in {item["clip_id"] for item in ranked["Santiago Chile city"]}


def test_santiago_title_passes_text_gate() -> None:
    clip = {
        "title": "Aerial view of santiago de chile city",
        "source": {"source_title": "Santiago skyline"},
    }
    assert sufficient_query_match(clip, ["Santiago Chile city"])


def test_assignment_uses_quality_fallback_instead_of_needs_import() -> None:
    assignment = assign_scenes([_job("Zzyzx Unknown", "Zzyzx Unknown")], _index())[0]
    assert assignment["status"] == "catalog"
    assert assignment["primary"]["fallback"] is True
