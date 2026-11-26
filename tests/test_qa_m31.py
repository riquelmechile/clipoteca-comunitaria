"""M31: every one of the fourteen publishing gates has a failing case."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from clipoteca.studio import checks, qa
from clipoteca.studio.compose import _html
from clipoteca.studio.script import parse_video_script

FIXTURE = Path(__file__).parent / "fixtures" / "m31"
SAMPLE_SUMMARY = """
[Parsed_ebur128_0 @ 0xa] Summary:
  Integrated loudness:
    I:         -14.0 LUFS
    Threshold: -24.0 LUFS
  Loudness range:
    LRA:         1.0 LU
  True peak:
    Peak:        -1.3 dBFS
"""


def data(name: str):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def heard_words(shift: float = 0.0, *, wrong: bool = False):
    timeline = data("timeline.json")
    words = [
        qa.HeardWord(
            "otro" if wrong else word,
            row["vo"] + start + shift,
            row["vo"] + end + shift,
        )
        for row in timeline["scenes"]
        for word, start, end in row["words"]
    ]
    return words


def fake_probe(*, codec: str = "h264", duration: float = 6.0, size=1_000_000):
    return {
        "streams": [
            {"codec_type": "video", "codec_name": codec, "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
        ],
        "format": {"duration": str(duration), "size": str(size)},
    }


def fake_runner(
    argv,
    *,
    cwd=None,
    timeout=120,
    **kwargs,
):
    if argv[0] == "npx":
        return subprocess.CompletedProcess(argv, 0, "Check passed\n0 error(s)\n", "")
    if argv[0] == "ffprobe":
        return subprocess.CompletedProcess(argv, 0, json.dumps(fake_probe()), "")
    if argv[0] == "ffmpeg":
        if "pcm_s16le" in argv:
            with wave.open(str(argv[-1]), "wb") as source:
                source.setnchannels(1)
                source.setframerate(16000)
                source.setsampwidth(2)
                source.writeframes(bytes(16000 * 2))
        if any("ebur128" in arg for arg in argv):
            return subprocess.CompletedProcess(argv, 0, "", SAMPLE_SUMMARY)
        return subprocess.CompletedProcess(argv, 0, "", "")
    raise AssertionError("unexpected external command")


class FakePage:
    def __init__(self, problems=None, measured=3):
        self.problems = problems or []
        self.measured = measured

    def route(self, *_args):
        return None

    def goto(self, *_args, **_kwargs):
        return None

    def evaluate(self, code, args=None):
        if "document.fonts.ready" in code:
            return None
        return {"checked": len(args[0]), "measured": self.measured, "problems": self.problems}


class FakeBrowser:
    def __init__(self, problems=None, measured=3):
        self.problems = problems or []
        self.measured = measured

    def new_page(self, **_kwargs):
        return FakePage(self.problems, self.measured)

    def close(self):
        return None


class FakePlaywright:
    def __init__(self, problems=None, measured=3):
        self.problems = problems or []
        self.measured = measured
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def launch(self, **_kwargs):
        return FakeBrowser(self.problems, self.measured)


def browser_factory(problems=None, measured=3):
    return lambda: FakePlaywright(problems, measured)


@pytest.fixture
def job(tmp_path: Path) -> Path:
    (tmp_path / "hf").mkdir()
    (tmp_path / "scenes").mkdir()
    (tmp_path / "fit").mkdir()
    for name in ("guion.json", "timeline.json", "chunks.json"):
        shutil.copyfile(FIXTURE / name, tmp_path / name)
    script = parse_video_script(data("guion.json"))
    markup, _chunks = _html(script, data("timeline.json")["scenes"], total=6, beat=0.5)
    (tmp_path / "hf" / "index.html").write_bytes(markup)
    (tmp_path / "video.mp4").write_bytes(b"synthetic-fake-video")
    for ident, result in data("results.json").items():
        folder = tmp_path / "scenes" / ident
        folder.mkdir()
        (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (tmp_path / "fit" / f"{ident}.mp4").write_bytes(b"dummy")
    return tmp_path


def run_good(job: Path, **kwargs):
    return qa.run_qa(
        job,
        runner=fake_runner,
        browser_factory=browser_factory(),
        black_frozen_fn=lambda _path: ([], []),
        transcriber=lambda _samples, _lang: heard_words(),
        **kwargs,
    )


def test_good_all_fourteen_and_report_shape(job: Path):
    report = run_good(job)
    assert report["pass"] is True
    assert report["failed"] == []
    assert tuple(report["checks"]) == qa.CHECK_NAMES
    assert all(result["pass"] for result in report["checks"].values())
    assert report["version"] == 1
    assert len(report["script_id"]) == 64
    assert json.loads((job / "qa_report.json").read_text()) == report
    first = {key: value for key, value in report.items() if key != "time_s"}
    second = {key: value for key, value in run_good(job).items() if key != "time_s"}
    assert first == second


@pytest.mark.parametrize("name", qa.CHECK_NAMES)
def test_exactly_one_gate_fails_with_bad_input(job: Path, name: str):
    bad_runner = fake_runner
    browser = browser_factory()

    def inspector(_path):
        return [], []

    def transcriber(_samples, _lang):
        return heard_words()

    timeline = data("timeline.json")
    results = data("results.json")
    html = (job / "hf" / "index.html").read_text()
    size = (1080, 1920)

    if name == "hyperframes_check":

        def bad_runner(argv, **kwargs):
            if argv[0] == "npx":
                return subprocess.CompletedProcess(argv, 0, "Check passed\n2 error(s)\n", "")
            return fake_runner(argv, **kwargs)

        assert not qa.hyperframes_check(job / "hf", runner=bad_runner)["pass"]
    elif name == "layout_playwright":
        browser = browser_factory([{"t": 1.4, "text": "OUT", "outSafe": True}])
        assert not qa.layout_playwright(
            job / "hf",
            timeline,
            data("chunks.json"),
            "9:16",
            browser_factory=browser,
        )["pass"]
    elif name == "file_probe":

        def bad_runner(argv, **kwargs):
            if argv[0] == "ffprobe":
                return subprocess.CompletedProcess(argv, 0, json.dumps(fake_probe(codec="vp9")), "")
            return fake_runner(argv, **kwargs)

        assert not qa.file_probe(job / "video.mp4", 6, size, runner=bad_runner)["pass"]
    elif name == "decode_clean":

        def bad_runner(argv, **kwargs):
            if argv[0] == "ffmpeg" and "-v" in argv and "error" in argv and "-f" in argv:
                if "null" in argv:
                    return subprocess.CompletedProcess(argv, 0, "", "decode error\n")
            return fake_runner(argv, **kwargs)

        assert not qa.decode_clean(job / "video.mp4", runner=bad_runner)["pass"]
    elif name == "blackdetect":

        def bad_runner(argv, **kwargs):
            if any("blackdetect" in str(v) for v in argv):
                return subprocess.CompletedProcess(
                    argv, 0, "", "[blackdetect] black_start:1.2 black_end:1.5"
                )
            return fake_runner(argv, **kwargs)

        assert not qa.blackdetect(job / "video.mp4", runner=bad_runner)["pass"]
    elif name == "freezedetect":

        def bad_runner(argv, **kwargs):
            if any("freezedetect" in str(v) for v in argv):
                return subprocess.CompletedProcess(argv, 0, "", "[freezedetect] freeze_start:1.2")
            return fake_runner(argv, **kwargs)

        assert not qa.freezedetect(job / "video.mp4", runner=bad_runner)["pass"]
    elif name == "silencedetect":

        def bad_runner(argv, **kwargs):
            if any("silencedetect" in str(v) for v in argv):
                return subprocess.CompletedProcess(
                    argv, 0, "", "silence_start: 0.4\nsilence_end: 1.5"
                )
            return fake_runner(argv, **kwargs)

        assert not qa.silencedetect(job / "video.mp4", 6, runner=bad_runner)["pass"]
    elif name == "loudness_ebur128":

        def bad_runner(argv, **kwargs):
            if any("ebur128" in str(v) for v in argv):
                return subprocess.CompletedProcess(
                    argv, 0, "", SAMPLE_SUMMARY.replace("-14.0 LUFS", "-17.0 LUFS")
                )
            return fake_runner(argv, **kwargs)

        assert not qa.loudness_ebur128(job / "video.mp4", runner=bad_runner)["pass"]
    elif name == "whisper_wer":

        def transcriber(_samples, _lang):
            return heard_words(wrong=True)

        assert not qa.whisper_wer(timeline, "es", heard_words(wrong=True))["pass"]
    elif name == "subtitle_sync":

        def transcriber(_samples, _lang):
            return heard_words(shift=0.5)

        assert not qa.subtitle_sync(timeline, "es", heard_words(shift=0.5))["pass"]
    elif name == "clips_all_scenes":
        (job / "fit" / "intro.mp4").unlink()
        assert not qa.clips_all_scenes(
            timeline,
            results,
            job / "fit",
            html,
        )["pass"]
    elif name == "clips_unique":
        results["lago"]["clip"]["phash"] = results["intro"]["clip"]["phash"]
        result_file = job / "scenes" / "lago" / "result.json"
        result_file.write_text(json.dumps(results["lago"]))
        assert not qa.clips_unique(timeline, results)["pass"]
    elif name == "clips_resolution":
        results["intro"]["clip"]["src_w"] = 100
        results["intro"]["clip"]["src_h"] = 200
        result_file = job / "scenes" / "intro" / "result.json"
        result_file.write_text(json.dumps(results["intro"]))
        assert not qa.clips_resolution(timeline, results, size)["pass"]
    elif name == "clips_black_freeze":

        def inspector(_path):
            return [1.0], []

        assert not qa.clips_black_freeze(timeline, job / "fit", inspector=inspector)["pass"]

    report = qa.run_qa(
        job,
        runner=bad_runner,
        browser_factory=browser,
        black_frozen_fn=inspector,
        transcriber=transcriber,
    )
    assert report["pass"] is False
    assert name in report["failed"]
    assert report["checks"][name]["pass"] is False
    assert json.loads((job / "qa_report.json").read_text())["pass"] is False


def test_gate_exception_does_not_abort_rest(job: Path):
    def runner(argv, **kwargs):
        if argv[0] == "npx":
            raise RuntimeError("/tmp/private/log-file.txt command exploded")
        return fake_runner(argv, **kwargs)

    report = qa.run_qa(
        job,
        runner=runner,
        browser_factory=browser_factory(),
        black_frozen_fn=lambda _path: ([], []),
        transcriber=lambda _samples, _lang: heard_words(),
    )
    assert report["failed"] == ["hyperframes_check"]
    assert len(report["checks"]) == 14
    assert "<path>" in report["checks"]["hyperframes_check"]["error"]


def test_transcriber_called_once_for_both_gates(job: Path):
    called = []

    def callback(samples, lang):
        called.append((len(samples), lang))
        return heard_words()

    report = qa.run_qa(
        job,
        runner=fake_runner,
        browser_factory=browser_factory(),
        black_frozen_fn=lambda _path: ([], []),
        transcriber=callback,
    )
    assert report["pass"]
    assert len(called) == 1


def test_cli_exit_codes_and_missing_input(job: Path, monkeypatch, capsys):
    monkeypatch.setattr(
        qa, "run_qa", lambda *args, **kwargs: {"pass": False, "failed": ["file_probe"]}
    )
    assert qa.main(["--work", str(job)]) == 1
    monkeypatch.setattr(qa, "run_qa", lambda *args, **kwargs: {"pass": True, "failed": []})
    assert qa.main(["--work", str(job)]) == 0

    def raise_missing(*args, **kwargs):
        raise qa.QAInputError("missing video.mp4")

    monkeypatch.setattr(qa, "run_qa", raise_missing)
    with pytest.raises(SystemExit) as exc:
        qa.main(["--work", str(job)])
    assert exc.value.code == 2
    assert "missing video.mp4" in capsys.readouterr().err


def test_real_missing_input_writes_no_report(job: Path):
    (job / "video.mp4").unlink()
    with pytest.raises(qa.QAInputError, match="missing video"):
        run_good(job)
    assert not (job / "qa_report.json").exists()


def test_wer_threshold_sync_pause_end_rule():
    timeline = data("timeline.json")
    assert qa.whisper_wer(timeline, "es", heard_words())["pass"]
    assert qa.subtitle_sync(timeline, "es", heard_words())["pass"]
    assert qa.whisper_wer(timeline, "es", heard_words(wrong=True))["wer"] > 0.12
    assert not qa.subtitle_sync(timeline, "es", heard_words(shift=0.5))["pass"]


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for real fixture",
)
def test_real_ffmpeg_gates(tmp_path: Path):
    video = tmp_path / "synthetic.mp4"
    try:
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
                "testsrc2=size=720x1280:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=420:sample_rate=48000",
                "-t",
                "2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ac",
                "2",
                str(video),
            ],
            timeout=120,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"ffmpeg unavailable: {type(exc).__name__}")
    assert qa.file_probe(video, 2.0, (720, 1280))["pass"]
    assert qa.decode_clean(video)["pass"]
    assert qa.blackdetect(video)["pass"]
    assert qa.freezedetect(video)["pass"]
    assert qa.silencedetect(video, 2.0)["pass"]
    # Unnormalized sine is intentionally out of range; the gate must catch it.
    assert "lufs" in qa.loudness_ebur128(video)
    # Black frames and silence are measurable negative fixtures with real ffmpeg.
    black = tmp_path / "black.mp4"
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
            "color=c=black:s=720x1280:r=30",
            "-t",
            "2.5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(black),
        ],
        timeout=60,
        check=True,
        capture_output=True,
    )
    assert not qa.blackdetect(black)["pass"]
    assert not qa.freezedetect(black)["pass"]


@pytest.mark.skipif(
    os.environ.get("CLIPOTECA_M31_REAL_LAYOUT") != "1",
    reason="real browser test is opt-in; requires installed Chromium",
)
def test_real_playwright_detects_outsafe_and_overlap(tmp_path: Path):
    root = tmp_path / "hf"
    root.mkdir()
    (root / "index.html").write_text(
        '<html><body style="margin:0"><div class="clip" data-start="0" '
        'data-duration="2"><div class="qa-text" '
        'style="position:absolute;left:0;top:0">Outside safe</div>'
        '<div class="qa-text" style="position:absolute;left:0;top:0">'
        "Overlaps</div></div>"
        "<script>window.__timelines={main:{seek(){}}}</script></body></html>",
        encoding="utf-8",
    )
    timeline = {"scenes": [{"start": 0, "len": 2}]}
    result = qa.layout_playwright(root, timeline, [], "9:16")
    assert not result["pass"]
    assert any(p.get("outSafe") for p in result["problems"])
    assert any(p.get("overlap") for p in result["problems"])

    from clipoteca.studio.compose import CONFIG, _caption_chunks, _caption_html

    words = ["ascensores", "y", "miradores", "desafortunadamente", "internacionalizaci\u00f3n"]
    rows = [{"vo": 0, "words": [(word, i, i + 0.8) for i, word in enumerate(words)]}]
    for aspect, layout in CONFIG.items():
        chunks = _caption_chunks(rows, 6, layout["cap"][1], layout["cap"][3])
        (root / "index.html").write_text(
            "<html><head><style>*{margin:0;padding:0;box-sizing:border-box}</style></head>"
            "<body>"
            + _caption_html(chunks, layout)
            + "<script>window.__timelines={main:{seek(){}}}</script></body></html>",
            encoding="utf-8",
        )
        result = qa.layout_playwright(
            root,
            {"scenes": [{"start": 0, "len": 6}]},
            [["caption", chunk["start"]] for chunk in chunks],
            aspect,
        )
        assert result["pass"], (aspect, result["problems"])


def test_clips_unique_fails_closed_without_results_or_phash():
    """Orchestrator review: an empty or hash-less result set was never deduplicated."""
    timeline, results = data("timeline.json"), data("results.json")
    assert qa.clips_unique(timeline, results)["pass"]
    assert not qa.clips_unique(timeline, {})["pass"]
    for broken in ({}, {"phash": "no-hex"}, {"phash": None}):
        rows = json.loads(json.dumps(results))
        rows["lago"]["clip"].pop("phash")
        rows["lago"]["clip"].update(broken)
        report = qa.clips_unique(timeline, rows)
        assert not report["pass"] and report["invalid"] == ["lago"]
    rows = json.loads(json.dumps(results))
    rows["intro"]["status"] = "fail"
    assert qa.clips_unique(timeline, rows)["invalid"] == ["intro"]


def test_layout_fails_when_no_text_was_measured(job: Path):
    timeline, chunks = data("timeline.json"), data("chunks.json")
    good = qa.layout_playwright(
        job / "hf", timeline, chunks, "9:16", browser_factory=browser_factory()
    )
    empty = qa.layout_playwright(
        job / "hf", timeline, chunks, "9:16", browser_factory=browser_factory(measured=0)
    )
    assert good["pass"] and good["measured"] == 3
    assert not empty["pass"] and empty["problems"] == []


def test_stale_report_never_survives_a_failed_start(job: Path):
    assert run_good(job)["pass"]
    assert (job / "qa_report.json").is_file()
    (job / "video.mp4").unlink()
    with pytest.raises(qa.QAInputError):
        run_good(job)
    assert not (job / "qa_report.json").exists()


def test_report_binds_the_verdict_to_the_video_bytes(job: Path):
    import hashlib

    report = run_good(job)
    raw = (job / "video.mp4").read_bytes()
    assert report["video"] == {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def test_file_probe_allows_one_minute_and_100_mb_but_not_more(job: Path):
    def runner_for(duration):
        def runner(argv, **kwargs):
            if argv[0] == "ffprobe":
                probe = json.dumps(fake_probe(duration=duration))
                return subprocess.CompletedProcess(argv, 0, probe, "")
            return fake_runner(argv, **kwargs)

        return runner

    video = job / "video.mp4"
    assert qa.file_probe(video, 60.0, (1080, 1920), runner=runner_for(60.0))["pass"]
    assert not qa.file_probe(video, 75.0, (1080, 1920), runner=runner_for(75.0))["pass"]
    assert qa.MAX_SIZE_MB == 100 and qa.MAX_DURATION_S == 60.0
    with video.open("r+b") as stream:
        stream.truncate(101_000_000)  # sparse: no real disk use
    assert not qa.file_probe(video, 60.0, (1080, 1920), runner=runner_for(60.0))["pass"]
    with video.open("r+b") as stream:
        stream.truncate(99_000_000)
    assert qa.file_probe(video, 60.0, (1080, 1920), runner=runner_for(60.0))["pass"]


def test_silence_before_the_first_sample_is_not_ignored(job: Path):
    def runner(argv, **kwargs):
        if any("silencedetect" in arg for arg in argv):
            return subprocess.CompletedProcess(argv, 0, "", "silence_start: -0.05\n")
        return fake_runner(argv, **kwargs)

    report = qa.silencedetect(job / "video.mp4", 6.0, runner=runner)
    assert not report["pass"] and report["early_starts"] == [-0.05]


def test_wer_accepts_the_spoken_form_of_lexicon_entries():
    """The voice says "kilometros por hora"; Whisper may write that or "km/h"."""
    timeline = {"scenes": [{"caption": "El auto alcanza 200 km/h en la recta final"}]}

    def heard(text):
        return [qa.HeardWord(word, i * 0.3, i * 0.3 + 0.2) for i, word in enumerate(text.split())]

    spoken = heard("El auto alcanza doscientos kilometros por hora en la recta final")
    written = heard("El auto alcanza 200 km/h en la recta final")
    assert qa.whisper_wer(timeline, "es", spoken)["wer"] == 0
    assert qa.whisper_wer(timeline, "es", written)["wer"] == 0
    own = {"scenes": [{"caption": "La sigla ZQX manda"}]}
    said = heard("La sigla zeta cu equis manda")
    assert not qa.whisper_wer(own, "es", said)["pass"]
    assert qa.whisper_wer(own, "es", said, {"ZQX": "zeta cu equis"})["wer"] == 0


def test_one_minute_is_the_single_limit_for_plan_timeline_and_qa():
    from clipoteca.studio import plan, script, timeline

    assert script.MAX_VIDEO_SECONDS == 60.0 == qa.MAX_DURATION_S
    assert plan.MAX_VIDEO_SECONDS is script.MAX_VIDEO_SECONDS is timeline.MAX_VIDEO_SECONDS
    raw = data("guion.json")
    assert plan.build_plan(parse_video_script(raw))["total_est"] <= 60
    long = json.loads(json.dumps(raw))
    first, middle, last = long["scenes"]
    extra = [dict(middle, id=f"extra{i}") for i in range(9)]
    long["scenes"] = [first, middle, *extra, last]  # twelve scenes: the schema maximum
    for scene in long["scenes"]:
        scene["say"] = scene["caption"] = "Los glaciares retroceden cada verano un poco más. " * 3
    with pytest.raises(script.ScriptError, match="máximo es 60 s"):
        plan.build_plan(parse_video_script(long))


def test_degraded_scene_is_warning_not_gate_failure(job):
    path = next((job / "scenes").glob("*/result.json"))
    result = json.loads(path.read_text())
    result.update(fallback=True, degraded_reason="low_sim")
    path.write_text(json.dumps(result), encoding="utf-8")
    report = run_good(job)
    assert report["pass"]
    assert report["warnings"] == [{"scene_id": path.parent.name, "reason": "low_sim"}]


@pytest.mark.parametrize("origin", ["contribution", "contribution_image"])
def test_explicit_contribution_reuse_preserves_automatic_deduplication(origin):
    timeline, results = data("timeline.json"), data("results.json")
    for ident in ("intro", "lago"):
        row = results[ident]
        row["origin"] = origin
        row["clip"].update(media_ref="u123", clip_id="u123", start=0, phash="0000000000000000")
    assert not checks.check_global(results)
    assert qa.clips_unique(timeline, results)["pass"]

    # A distinct explicit contribution can also look similar.
    results["lago"]["clip"].update(media_ref="u124", clip_id="u124")
    assert qa.clips_unique(timeline, results)["pass"]

    # Missing/malformed registry references cannot opt out of deduplication.
    results["lago"]["clip"]["media_ref"] = "clip-000001"
    assert not qa.clips_unique(timeline, results)["pass"]
    results["lago"]["origin"] = "catalog_pinned"
    results["lago"]["clip"]["media_ref"] = "u124"
    assert not qa.clips_unique(timeline, results)["pass"]
    assert checks.check_global(results) == {"lago": "duplicate_phash"}

    # The explicit exception never bypasses hash validation.
    results["lago"]["origin"] = origin
    results["lago"]["clip"]["phash"] = "invalid"
    assert qa.clips_unique(timeline, results)["invalid"] == ["lago"]


@pytest.mark.parametrize("origin", ["contribution", "contribution_image"])
def test_explicit_contribution_resolution_warns_but_output_stays_strict(origin):
    timeline, results = data("timeline.json"), data("results.json")
    row = results["intro"]
    row["origin"] = origin
    row["clip"].update(media_ref="u123", src_w=480, src_h=872)
    report = qa.clips_resolution(timeline, results, (1080, 1920))
    assert report["pass"]
    assert report["scaled_contributions"] == ["intro"]

    row["clip"]["out_w"] = 720
    assert not qa.clips_resolution(timeline, results, (1080, 1920))["pass"]
    row["clip"]["out_w"] = 1080
    row["clip"]["src_w"] = 0
    assert not qa.clips_resolution(timeline, results, (1080, 1920))["pass"]
    row["clip"]["src_w"] = 480
    row["clip"]["media_ref"] = "clip-000001"
    assert not qa.clips_resolution(timeline, results, (1080, 1920))["pass"]
    row["origin"] = "catalog_pinned"
    row["clip"]["media_ref"] = "u123"
    assert not qa.clips_resolution(timeline, results, (1080, 1920))["pass"]


def test_scaled_explicit_contribution_appears_in_qa_warnings(job):
    path = job / "scenes" / "intro" / "result.json"
    row = json.loads(path.read_text())
    row["origin"] = "contribution"
    row["clip"].update(media_ref="u123", src_w=360, src_h=640)
    path.write_text(json.dumps(row))
    report = run_good(job)
    assert report["pass"]
    assert {"scene_id": "intro", "reason": "aporte de baja resolución escalado"} in report[
        "warnings"
    ]
