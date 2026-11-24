"""M28 isolated scene runner integration with fake fetch, CLIP and OCR."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio.clipscene import ClipSceneError, clip_scene, main
from clipoteca.studio import checks, clipscene, clipscore

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class FakeScorer:
    """Unit synthetic embeddings: prompt wins against generic negatives."""

    @staticmethod
    def text_emb(texts):
        out = np.zeros((len(texts), 512), dtype=np.float32)
        if texts:
            out[:, 0 if len(texts) == 1 else 1] = 1
        return out

    @staticmethod
    def image_emb(frames):
        out = np.zeros((len(frames), 512), dtype=np.float32)
        out[:, 0] = 1
        return out

    @staticmethod
    def frame_metrics(frames):
        n = len(frames)
        return (
            np.full(n, 200.0),
            np.full(n, 120.0),
            np.zeros(n),
            np.full(n, 12.0),
        )


def _fixture(path: Path, seconds: int = 8) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-t",
            str(seconds),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg/ffprobe unavailable")
    pytest.importorskip("cv2")
    directory = tmp_path_factory.mktemp("m28-clip")
    path = directory / "motion.mp4"
    _fixture(path)
    return path


def _candidate(src: Path, clip_id: str = "clip-a", **overrides):
    source = {
        "clip_id": clip_id,
        "asset_url": f"https://example.invalid/{clip_id}.mp4",
        "source_url": f"https://example.invalid/{clip_id}",
        "score": 4.5,
        "matched": ["waterfall"],
        "duration": 8,
        "width": 1280,
        "height": 720,
        "fit": "window",
        "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "size_bytes": src.stat().st_size,
    }
    return {**source, **overrides}


def _job(aspect="9:16", length=4.0):
    return {
        "version": 1,
        "script_id": "fixture",
        "id": "scene-1",
        "aspect": aspect,
        "len_fetch": length,
        "prompt": "a waterfall in the mountains",
        "negatives": ["a person talking", "a photo of food"],
        "min_sim": 0.22,
        "min_prob": 0.5,
        "min_short_side": 720,
    }


def _fetch(src: Path):
    def read(_manifest, destination):
        shutil.copyfile(src, destination)

    return read


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_scene_real_clip_1080x1920_and_metadata(source_clip: Path, tmp_path: Path):
    job = _job()
    assignment = {
        "status": "catalog",
        "primary": _candidate(source_clip),
        "backups": [],
    }
    report = clip_scene(
        job,
        assignment,
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0.001),
    )
    assert report["status"] == "ok"
    assert report["used"] == "primary" and report["tried"] == []
    assert report["clip"]["clip_id"] == "clip-a"
    assert report["clip"]["len"] == job["len_fetch"]
    assert report["clip"]["out_dur"] >= 4
    assert report["clip"]["fit"] == "window"
    assert report["clip"]["src_w"] == 1280
    assert report["clip"]["src_h"] == 720
    assert report["clip"]["out_w"] == 1080
    assert report["clip"]["out_h"] == 1920
    assert len(report["clip"]["crop"]) == 4
    assert len(report["clip"]["phash"]) == 16
    assert report["time_s"] >= 0
    assert json.loads((tmp_path / "result.json").read_text()) == report
    assert (tmp_path / "clip.mp4").is_file()
    stream = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(tmp_path / "clip.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    entries = json.loads(stream.stdout)["streams"]
    assert len(entries) == 1 and entries[0]["codec_type"] == "video"
    assert entries[0]["pix_fmt"] == "yuv420p"
    assert entries[0]["r_frame_rate"] == "30/1"
    assert checks.black_frozen(tmp_path / "clip.mp4") == ([], [])


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_preview_ocr_rejects_primary_uses_backup(source_clip: Path, tmp_path: Path):
    primary, backup = _candidate(source_clip), _candidate(source_clip, "clip-b")
    assignment = {
        "status": "catalog",
        "primary": primary,
        "backups": [backup],
    }

    class OneRejection:
        count = 0

        def __call__(self, _frame):
            self.count += 1
            if self.count == 1:
                return [([0, 0], "SAMPLE TEXT", 0.99)], 0.1
            return [], 0.1

    report = clip_scene(
        _job("1:1", 3),
        assignment,
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=OneRejection(),
    )
    assert report["status"] == "ok"
    assert report["used"] == "backup"
    assert report["clip"]["clip_id"] == "clip-b"
    assert report["tried"] == [{"clip_id": "clip-a", "reason": "text_in_preview"}]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_excluded_primary_uses_backup(source_clip: Path, tmp_path: Path):
    assignment = {
        "status": "catalog",
        "primary": _candidate(source_clip),
        "backups": [_candidate(source_clip, "clip-b")],
    }
    report = clip_scene(
        _job("1:1", 3),
        assignment,
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0),
        exclude=["clip-a"],
    )
    assert report["status"] == "ok"
    assert report["used"] == "backup"
    assert report["tried"] == [{"clip_id": "clip-a", "reason": "excluded"}]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_bad_sha_or_download_error_then_fail(source_clip: Path, tmp_path: Path):
    bad = _candidate(source_clip, sha256="0" * 64)
    report = clip_scene(
        _job(),
        {"status": "catalog", "primary": bad, "backups": []},
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: [],
    )
    assert report["status"] == "fail"
    assert report["reason"] == "no_candidate"
    assert report["tried"] == [{"clip_id": "clip-a", "reason": "download_failed"}]
    assert not (tmp_path / "clip.mp4").exists()


def test_needs_import_and_empty_catalog_always_write_fail(tmp_path: Path):
    for status in ("needs_import", "catalog"):
        report = clip_scene(
            _job(),
            {"status": status, "primary": None, "backups": []},
            tmp_path,
            scorer=FakeScorer(),
            ocr=lambda _: [],
        )
        assert report["status"] == "fail"
        assert report["reason"] == ("needs_import" if status == "needs_import" else "no_candidate")
        assert report["tried"] == []
        assert not (tmp_path / "clip.mp4").exists()
        assert json.loads((tmp_path / "result.json").read_text()) == report


def test_rejects_unsafe_ids_before_writes(tmp_path: Path):
    for scene_id in ("../unsafe", "a/b", "..", "bad.name", ""):
        with pytest.raises(ClipSceneError, match="unsafe"):
            clip_scene(
                {**_job(), "id": scene_id},
                {"status": "needs_import"},
                tmp_path,
            )
    assert not list(tmp_path.iterdir())


def test_cli_needs_import_code_3_and_invalid_json_code_2(tmp_path: Path):
    jobfile, assignmentfile = tmp_path / "job.json", tmp_path / "assignment.json"
    jobfile.write_text(json.dumps(_job()))
    assignmentfile.write_text(json.dumps({"status": "needs_import"}))
    assert (
        main(
            [
                "--job",
                str(jobfile),
                "--assignment",
                str(assignmentfile),
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 3
    )
    assert json.loads((tmp_path / "out" / "result.json").read_text())["status"] == "fail"
    jobfile.write_text("{")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--job",
                str(jobfile),
                "--assignment",
                str(assignmentfile),
                "--out",
                str(tmp_path / "bad"),
            ]
        )
    assert exc.value.code == 2


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_local_verified_clip_uses_same_scene_gates(source_clip: Path, tmp_path: Path):
    root = tmp_path / "local-root"
    (root / "clips").mkdir(parents=True)
    shutil.copyfile(source_clip, root / "clips" / "imported.mp4")
    candidate = _candidate(source_clip, "imp-0123456789ab")
    candidate.pop("asset_url")
    candidate["local_path"] = "clips/imported.mp4"
    report = clip_scene(
        _job(),
        {"status": "imported", "primary": candidate, "backups": []},
        tmp_path / "processed",
        local_root=root,
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0.1),
    )
    assert report["status"] == "ok", report
    assert report["clip"]["imported"] is True
    assert report["clip"]["local_path"] == "clips/imported.mp4"
    assert "asset_url" not in report["clip"]
    assert (tmp_path / "processed" / "clip.mp4").is_file()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe unavailable")
def test_local_hash_mismatch_falls_back_to_catalog(source_clip: Path, tmp_path: Path):
    root = tmp_path / "local"
    root.mkdir()
    shutil.copyfile(source_clip, root / "asset.mp4")
    candidate = _candidate(source_clip, "imp-bad", sha256="a" * 64)
    candidate.pop("asset_url")
    candidate["local_path"] = "asset.mp4"
    backup = _candidate(source_clip, "clip-good")
    report = clip_scene(
        _job(),
        {"status": "imported", "primary": candidate, "backups": [backup]},
        tmp_path / "result",
        local_root=root,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: ([], 0.01),
    )
    assert report["status"] == "ok", report
    assert report["used"] == "backup"
    assert report["tried"][0]["reason"] == "hash_mismatch"
    assert "imported" not in report["clip"]


@pytest.mark.parametrize(
    "relative",
    [
        "../escaped.mp4",
        "/tmp/escaped.mp4",
        "clips/../escaped.mp4",
        "clips\\bad.mp4",
        "C:\\escaped.mp4",
    ],
)
def test_local_path_traversal_is_explicitly_rejected(
    tmp_path: Path,
    relative: str,
):
    root = tmp_path / "local"
    root.mkdir()
    candidate = {
        "clip_id": "imp-bad",
        "local_path": relative,
        "size_bytes": 1,
        "sha256": "0" * 64,
    }
    report = clip_scene(
        _job(),
        {"status": "imported", "primary": candidate, "backups": []},
        tmp_path / "out",
        local_root=root,
        scorer=FakeScorer(),
        ocr=lambda _: [],
    )
    assert report["status"] == "fail"
    assert report["tried"] == [{"clip_id": "imp-bad", "reason": "bad_local_path"}]


def test_local_symlink_and_duplicate_asset_selectors_rejected(tmp_path: Path):
    root = tmp_path / "local"
    root.mkdir()
    (root / "target.mp4").write_bytes(b"x")
    (root / "link.mp4").symlink_to(root / "target.mp4")
    for candidate, reason in (
        (
            {"clip_id": "imp-link", "local_path": "link.mp4", "sha256": "0" * 64, "size_bytes": 1},
            "bad_local_path",
        ),
        (
            {
                "clip_id": "imp-both",
                "local_path": "target.mp4",
                "asset_url": "https://example.org/a.mp4",
            },
            "invalid_candidate",
        ),
        ({"clip_id": "imp-neither"}, "invalid_candidate"),
    ):
        report = clip_scene(
            _job(),
            {"status": "imported", "primary": candidate, "backups": []},
            tmp_path / ("out-" + candidate["clip_id"]),
            local_root=root,
            scorer=FakeScorer(),
            ocr=lambda _: [],
        )
        assert report["tried"] == [{"clip_id": candidate["clip_id"], "reason": reason}]


@pytest.mark.parametrize(
    "floor, scores, expected",
    [
        (None, [-1.846, 2.0], "backup"),
        (0.0, [-1.846, -0.1], "fail"),
        (-2.0, [-1.846, 2.0], "primary"),
    ],
)
def test_score_floor(source_clip, tmp_path, monkeypatch, floor, scores, expected):
    job = _job("1:1", 3)
    if floor is not None:
        job["min_score"] = floor
    values = iter(scores)

    def choose(*args, **kwargs):
        return {"score": next(values), "start": 0.0, "sim": 0.2756, "prob": 0.853}, "ok"

    monkeypatch.setattr(clipscene, "choose_window", choose)
    report = clip_scene(
        job,
        {
            "status": "catalog",
            "primary": _candidate(source_clip),
            "backups": [_candidate(source_clip, "clip-b")],
        },
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: [],
    )
    if expected == "fail":
        assert report["reason"] == "no_candidate"
        assert [item["reason"] for item in report["tried"]] == ["low_score", "low_score"]
    else:
        assert report["status"] == "ok"
        assert report["used"] == expected
        assert [item["reason"] for item in report["tried"]] == (
            ["low_score"] if expected == "backup" else []
        )


@pytest.mark.parametrize("value", [True, "0", None, float("nan"), float("inf")])
def test_invalid_min_score(value, tmp_path):
    with pytest.raises(ClipSceneError, match="min_score"):
        clip_scene({**_job(), "min_score": value}, {"status": "needs_import"}, tmp_path)


def _cli_args(tmp_path, candidate, *, local_root=None):
    job, assignment = tmp_path / "job.json", tmp_path / "assignment.json"
    job.write_text(json.dumps(_job()), encoding="utf-8")
    assignment.write_text(
        json.dumps({"status": "imported" if local_root else "catalog", "primary": candidate}),
        encoding="utf-8",
    )
    args = ["--job", str(job), "--assignment", str(assignment), "--out", str(tmp_path / "out")]
    if local_root is not None:
        args.extend(["--local-root", str(local_root)])
    return args


@pytest.mark.parametrize("failure", ["scorer", "import", "ocr"])
def test_evaluator_error_is_not_no_candidate(tmp_path, monkeypatch, capsys, failure):
    original_import = clipscene.importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            if failure == "import":
                raise ImportError("private path /secret/model", name="cv2")
            return object()
        return original_import(name, *args, **kwargs)

    class BrokenScorer:
        def __init__(self, _path):
            if failure == "scorer":
                raise clipscore.ClipScoreError("missing local CLIP assets at /secret/model")

        def _sess(self):
            return {}

    def broken_ocr():
        raise RuntimeError("private path /secret/model")

    monkeypatch.setattr(clipscene.importlib, "import_module", fake_import)
    monkeypatch.setattr(clipscore, "ClipScorer", BrokenScorer)
    monkeypatch.setattr(checks, "get_ocr", broken_ocr)
    candidate = {"clip_id": "clip-a", "asset_url": "https://example.invalid/a.mp4"}
    args = _cli_args(tmp_path, candidate)
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text('{"status":"ok"}', encoding="utf-8")
    with pytest.raises(ClipSceneError, match="CLIPOTECA_STUDIO_MODELS"):
        clip_scene(_job(), {"status": "catalog", "primary": candidate}, out)
    assert not (out / "result.json").exists()
    (out / "result.json").write_text('{"status":"ok"}', encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "CLIPOTECA_STUDIO_MODELS" in error and "--extra studio" in error
    assert "/secret/model" not in error
    assert not (out / "result.json").exists()
    expected = {
        "scorer": "vision_model_quantized.onnx",
        "import": "cv2",
        "ocr": "rapidocr_onnxruntime",
    }
    assert expected[failure] in error


@pytest.mark.parametrize(
    "exception, reason",
    [
        (checks.DecodeError("hidden path"), "decode_failed"),
        (checks.InspectionTimeout("hidden path"), "timeout"),
        (ValueError("hidden path"), "processing_failed"),
    ],
)
def test_precise_candidate_failure(tmp_path, monkeypatch, exception, reason):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"verified bytes")

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(checks, "probe_video", fail)
    report = clip_scene(
        _job(),
        {"status": "catalog", "primary": _candidate(source)},
        tmp_path / "out",
        fetch=_fetch(source),
        scorer=FakeScorer(),
        ocr=lambda _: [],
    )
    assert report["tried"] == [{"clip_id": "clip-a", "reason": reason}]
    assert "hidden path" not in json.dumps(report)


@pytest.mark.parametrize("bad", [False, True])
def test_cli_local_root(source_clip, tmp_path, monkeypatch, bad):
    actual = tmp_path / "actual"
    root = actual / "local"
    root.mkdir(parents=True)
    shutil.copyfile(source_clip, root / "clip.mp4")
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    candidate = _candidate(source_clip, "imp-0123456789ab")
    candidate.pop("asset_url")
    candidate["local_path"] = "../clip.mp4" if bad else "clip.mp4"
    monkeypatch.setattr(
        clipscene, "_initialize_evaluators", lambda *_: (FakeScorer(), lambda _: [])
    )
    args = _cli_args(tmp_path, candidate, local_root=alias / "local")
    assert main(args) == (3 if bad else 0)
    report = json.loads((tmp_path / "out" / "result.json").read_text())
    if bad:
        assert report["tried"] == [{"clip_id": candidate["clip_id"], "reason": "bad_local_path"}]
    else:
        assert report["clip"]["imported"] is True
        assert report["clip"]["local_path"] == "clip.mp4"


def test_preview_analysis_cap(source_clip, tmp_path, monkeypatch):
    # The actual short source is enough to verify the reported truncation contract.
    monkeypatch.setattr(clipscene, "MAX_ANALYZED_SECONDS", 5.0)
    report = clip_scene(
        _job("1:1", 3),
        {"status": "catalog", "primary": _candidate(source_clip)},
        tmp_path,
        fetch=_fetch(source_clip),
        scorer=FakeScorer(),
        ocr=lambda _: [],
    )
    assert report["status"] == "ok"
    assert report["analyzed_s"] == 5.0
    assert report["clip"]["start"] + report["clip"]["len"] <= 5.0
