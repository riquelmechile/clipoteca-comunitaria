"""M21: entirely offline fake-encoder regressions in es/en/zh/ru."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from clipoteca.media import embeddings as emb
from clipoteca.media import select
from clipoteca.media.embedding_store import (
    EmbeddingStore,
    decode_vector,
    new_index,
    vector_bytes,
)
from clipoteca.ingest import _candidate_windows, _embedding_result
from clipoteca.media.scenes import Scene

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "m21"


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class FakeEncoder:
    """Deterministic fake sharing a 512-D image/text embedding space."""

    def encode_text(self, text: str) -> np.ndarray:
        if text not in {"lluvia", "rain", "下雨", "дождь"}:
            raise ValueError("fixture uses only four test languages")
        vec = np.zeros(emb.DIMENSION, dtype=np.float32)
        vec[0] = 1
        return vec

    def encode_images(self, images: list[Image.Image]) -> np.ndarray:
        rows = []
        for image in images:
            brightness = np.asarray(image).mean()
            vec = np.zeros(emb.DIMENSION, dtype=np.float32)
            vec[0 if brightness >= 127 else 1] = 1.0
            rows.append(vec)
        return np.vstack(rows)


def scene_embeddings() -> emb.FrameEmbeddings:
    vectors = np.zeros((40, emb.DIMENSION), dtype=np.float32)
    vectors[:20, 1] = 1
    vectors[20:, 0] = 1
    return emb.FrameEmbeddings(np.arange(40, dtype=np.float32), vectors)


@pytest.mark.parametrize(
    "language,query", list(fixture("multilingual_scenes.json")["queries"].items())
)
def test_four_languages_choose_rain_scene_over_m14_quality(monkeypatch, language, query):
    """M14 favours visually busy but irrelevant first scene; CLIP chooses rain."""
    info = fixture("multilingual_scenes.json")
    assert language in ("es", "en", "zh", "ru")
    monkeypatch.setattr(
        select, "probe_media", lambda _path, timeout: SimpleNamespace(duration_seconds=40)
    )
    monkeypatch.setattr(select, "_sample_scores", lambda *_a, **_kw: [0.90] * 20 + [0.48] * 20)
    monkeypatch.setattr(
        select,
        "detect_scenes",
        lambda *_a, **_kw: [Scene(0, 20), Scene(20, 40)],
    )
    assert select.rank_scene_windows("synthetic.mp4", max_clips=1)[0].start == 0
    samples = scene_embeddings()
    scores = emb.cosine_scores(samples, FakeEncoder(), query)
    selected = select.rank_scene_windows(
        "synthetic.mp4",
        max_clips=1,
        semantic_scorer=lambda w: emb.window_similarity(samples, scores, start=w.start, end=w.end),
    )
    assert len(selected) == 1
    # The 2-second M14 candidate stride may trim the first frames of the
    # winning rain scene; its start must remain inside that scene.
    assert info["expected_scene_start"] <= selected[0].start < 24
    assert selected[0].duration <= 20


def test_centered_moving_average_three_seconds_and_window_boundary():
    data = fixture("timeline.json")
    times = np.asarray(data["times"], dtype=np.float32)
    cosines = np.asarray(data["cosine"], dtype=np.float32)
    smoothed = emb.moving_average(times, cosines, seconds=data["smoothing_seconds"])
    assert smoothed[2] == pytest.approx(data["expected_smooth_center_at_2"])
    samples = scene_embeddings()
    scores = np.zeros(40, dtype=np.float32)
    scores[20:] = 0.9
    assert emb.window_similarity(samples, scores, start=20, end=40) == pytest.approx(0.9)
    with pytest.raises(ValueError, match="within 20"):
        emb.window_similarity(samples, scores, start=0, end=21)


def test_180_second_threshold_and_hard_frame_cap():
    assert emb.sampling_fps(180) == 1
    assert emb.sampling_fps(180.01) == 0.5
    assert emb.MAX_FRAMES_PER_VIDEO <= 480
    assert emb.MAX_FFMPEG_OUTPUT == emb.MAX_FRAMES_PER_VIDEO * emb.FRAME_BYTES
    with pytest.raises(ValueError):
        emb.sampling_fps(float("nan"))


def test_extract_frames_uses_local_bounded_ffmpeg_and_fake_encoder(monkeypatch, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"local file placeholder for injected ffmpeg")
    monkeypatch.setattr(emb, "probe_media", lambda _p, timeout: SimpleNamespace(duration_seconds=4))
    captured = []

    def runner(cmd, **kwargs):
        captured.append((cmd, kwargs))
        first = bytes([20]) * emb.FRAME_BYTES
        second = bytes([220]) * emb.FRAME_BYTES
        return SimpleNamespace(stdout=first + second)

    sample = emb.extract_frame_embeddings(source, FakeEncoder(), runner=runner)
    assert sample.vectors.shape == (2, 512)
    assert list(sample.times) == [0, 1]
    assert sample.vectors[0, 1] == pytest.approx(1)
    assert sample.vectors[1, 0] == pytest.approx(1)
    args, options = captured[0]
    assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
    assert args[args.index("-frames:v") + 1] == str(emb.MAX_FRAMES_PER_VIDEO)
    assert options["timeout"] <= 150
    with pytest.raises(emb.EmbeddingError, match="local_file_required"):
        emb.extract_frame_embeddings("https://example.org/film.mp4", FakeEncoder())


def test_subsecond_and_zero_norm_samples_rejected():
    zero = np.zeros((1, 512), dtype=np.float32)
    with pytest.raises(emb.EmbeddingError, match="zero_norm"):
        emb.FrameEmbeddings(np.array([0], dtype=np.float32), zero)
    bad = np.ones((1, 511), dtype=np.float32)
    with pytest.raises(emb.EmbeddingError, match="invalid"):
        emb.FrameEmbeddings(np.array([0], dtype=np.float32), bad)


def test_clip_vector_float16_binary_index_and_m22_bruteforce_reader(tmp_path):
    frames = scene_embeddings()
    vector = emb.clip_vector(frames, start=20, end=40)
    store = EmbeddingStore(tmp_path / "data" / "embeddings")
    info = store.put("clip-000125", vector)
    assert info["path"] == "clip-000125.f16"
    assert (store.root / info["path"]).stat().st_size == 512 * 2
    assert store.index()["dtype"] == "float16-le"
    stored = store.read("clip-000125")
    assert stored.dtype == np.float32 and stored.shape == (512,)
    assert float(np.linalg.norm(stored)) == pytest.approx(1, abs=0.002)
    assert np.argmax(stored) == 0
    assert len(vector_bytes(vector)) == emb.DIMENSION * 2
    assert decode_vector(vector_bytes(vector))[0] == pytest.approx(1)
    assert new_index()["vectors"] == {}
    with pytest.raises(ValueError, match="invalid clip identifier"):
        store.put("../credentials", vector)
    with pytest.raises(emb.EmbeddingError):
        decode_vector(bytes(12))
    # Sidecar is not a new field in the Clip or clips.json schema.
    assert "vector" not in json.dumps(store.index().get("vectors", {}).get("clip-000125"))


def test_m14_fallback_is_explicit_in_job_and_ingest_result(monkeypatch):
    def missing_encoder():
        raise emb.EmbeddingUnavailable("embeddings_extra_not_installed")

    monkeypatch.setattr("clipoteca.ingest.load_encoder", missing_encoder)
    monkeypatch.setattr(
        "clipoteca.ingest.rank_scene_windows",
        lambda *_args, **_kwargs: [select.SourceWindow(0.0, 12.0)],
    )
    job = {}
    windows = _candidate_windows(
        Path("local-placeholder.mp4"),
        duration_limit=20,
        max_clips=1,
        job=job,
        origin_id="origin:test",
        query="lluvia",
    )
    assert windows == [select.SourceWindow(0.0, 12.0)]
    assert _embedding_result(job) == "embeddings_unavailable:m14_quality_fallback"
    assert job["source_warnings"]["origin:test"] == _embedding_result(job)


def test_semantic_mode_uses_m14_gate_and_records_result(monkeypatch):
    samples = scene_embeddings()
    monkeypatch.setattr("clipoteca.ingest.extract_frame_embeddings", lambda *_a: samples)
    seen = []

    def controlled_rank(*_a, **kwargs):
        seen.append(kwargs)
        scorer = kwargs["semantic_scorer"]
        assert scorer(select.SourceWindow(20, 40)) > scorer(select.SourceWindow(0, 20))
        return [select.SourceWindow(20, 40)]

    monkeypatch.setattr("clipoteca.ingest.rank_scene_windows", controlled_rank)
    cache = {}
    job = {}
    found = _candidate_windows(
        Path("fixture-placeholder.mp4"),
        duration_limit=20,
        max_clips=1,
        job=job,
        origin_id="origin:test",
        query="дождь",
        embedding_encoder=FakeEncoder(),
        embedding_frames=cache,
    )
    assert found[0].start == 20
    assert cache["origin:test"] is samples
    assert _embedding_result(job) == "embeddings_ranked"
    assert seen[0]["semantic_scorer"] is not None


@pytest.mark.skipif(
    not __import__("os").environ.get("CLIPOTECA_TEST_REAL_EMBEDDINGS"),
    reason="real weights require opt-in; CI never downloads model files",
)
def test_optional_real_model_weights_smoke():
    pytest.importorskip("torch")
    pytest.importorskip("sentence_transformers")
    encoder = emb.CLIPEncoder(device="cpu")
    assert emb.normalize_vector(encoder.encode_text("lluvia")).shape == (512,)
    assert encoder.encode_images([Image.new("RGB", (128, 128))]).shape == (1, 512)


def test_github_embedding_writer_cas_and_immutable_bytes_without_network():
    import base64
    import hashlib

    import httpx

    from clipoteca.media.embedding_store import GitHubEmbeddingWriter

    files: dict[str, bytes] = {}
    operations = []

    def handle(request: httpx.Request):
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            if name not in files:
                return httpx.Response(404)
            content = files[name]
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode(),
                    "sha": hashlib.sha1(content).hexdigest(),
                },
            )
        assert request.method == "PUT"
        operations.append(name)
        payload = json.loads(request.content)
        if name in files and payload.get("sha") != hashlib.sha1(files[name]).hexdigest():
            return httpx.Response(409)
        files[name] = base64.b64decode(payload["content"])
        return httpx.Response(201)

    vector = np.zeros(512, dtype=np.float32)
    vector[2] = 1.0
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        writer = GitHubEmbeddingWriter("riquelmechile/clipoteca-comunitaria", "test", client=client)
        writer.publish("clip-000200", vector)
        writer.publish("clip-000200", vector)
    assert operations == ["clip-000200.f16", "index.json"]
    assert len(files["clip-000200.f16"]) == 1024
    index = json.loads(files["index.json"])
    assert index["vectors"]["clip-000200"]["path"] == "clip-000200.f16"
    assert (
        index["vectors"]["clip-000200"]["sha256"]
        == hashlib.sha256(files["clip-000200.f16"]).hexdigest()
    )
