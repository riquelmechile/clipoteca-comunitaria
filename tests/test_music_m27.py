"""M27 music tests: deterministic offline unit tests plus optional real Chromium."""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from clipoteca.studio import music


FIXTURES = Path(__file__).parent / "fixtures/m27"
PLAN = json.loads((FIXTURES / "plan.json").read_text(encoding="utf-8"))
TIMELINE = json.loads((FIXTURES / "timeline.json").read_text(encoding="utf-8"))


def _write_audio(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    music._write_wav(path, audio)


def test_music_plan_exact_numbers_and_key() -> None:
    mp = music.music_plan(PLAN)
    assert mp["mood"] == "upbeat"
    assert mp["beat"] == 0.5
    assert mp["intro_end_est"] == 4
    assert mp["loop_len"] == 8
    assert mp["loop_start"] == 12
    assert mp["head_s"] == 20
    assert mp["bed_s"] == 51
    assert mp["total_est"] == 36
    assert len(mp["key"]) == 16
    assert int(mp["key"], 16) >= 0
    slower = json.loads(json.dumps(PLAN))
    slower["bpm"] = 96
    slower["music"]["bpm"] = 96
    slower["beat"] = 0.625
    slower["scenes"][1]["start_est"] = 3.75
    slower_mp = music.music_plan(slower)
    assert slower_mp["beat"] == 0.625
    assert slower_mp["intro_end_est"] == 3.75
    assert slower_mp["loop_len"] == 10
    assert slower_mp["loop_start"] == 13.75
    assert slower_mp["head_s"] == 23.75
    assert slower_mp["bed_s"] == 51.25


def test_music_plan_cache_key_identity() -> None:
    original = music.music_plan(PLAN)
    for change in ("mood", "bpm", "intro_end"):
        other = json.loads(json.dumps(PLAN))
        if change == "mood":
            other["music"]["mood"] = "epic"
        elif change == "bpm":
            other["bpm"] = other["music"]["bpm"] = 96
        else:
            other["scenes"][1]["start_est"] = 4.5
        assert music.music_plan(other)["key"] != original["key"]
    other = json.loads(json.dumps(PLAN))
    other["total_est"] += 40
    assert music.music_plan(other)["key"] == original["key"]


def test_music_plan_single_scene_intro_fallback() -> None:
    data = json.loads(json.dumps(PLAN))
    data["scenes"] = [data["scenes"][0]]
    assert music.music_plan(data)["intro_end_est"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("total_est"),
        lambda p: p.update(bpm=float("nan")),
        lambda p: p.update(bpm=float("inf")),
        lambda p: p.update(bpm=0),
        lambda p: p.update(bpm=True),
        lambda p: p.update(beat="bad"),
        lambda p: p.update(scenes=[]),
        lambda p: p["scenes"][1].update(start_est=float("inf")),
        lambda p: p["scenes"][1].pop("start_est"),
        lambda p: p["music"].update(mood="dubstep"),
        lambda p: p.pop("music"),
    ],
)
def test_music_plan_invalid_input_fails_as_music_error(mutation) -> None:
    value = json.loads(json.dumps(PLAN))
    mutation(value)
    with pytest.raises(music.MusicError, match="(plan|music|tempo)"):
        music.music_plan(value)


def test_write_bed_loops_second_phrase_with_exact_sample_count(tmp_path: Path) -> None:
    rate = music.SAMPLE_RATE
    source = np.linspace(-0.8, 0.8, int(0.6 * rate), dtype=np.float32)
    stereo = np.column_stack((source, -source))
    _write_audio(tmp_path / "head.wav", stereo)
    music.write_bed(tmp_path, loop_start=0.15, loop_len=0.2, length=0.8)
    result, sr = music._validated_audio(tmp_path / "bed.wav")
    a, n = round(0.15 * sr), round(0.2 * sr)
    assert len(result) == int(0.8 * sr)
    assert np.array_equal(result[:a], music._validated_audio(tmp_path / "head.wav")[0][:a])
    original = music._validated_audio(tmp_path / "head.wav")[0]
    assert np.array_equal(result[a : a + n], original[a : a + n])
    for k in (0, 1, 1000):
        assert np.array_equal(result[a + n + k], original[a + k])


def test_write_bed_rejects_missing_loop_and_malformed_wav(tmp_path: Path) -> None:
    _write_audio(tmp_path / "head.wav", np.zeros((3000, 2), dtype=np.float32))
    with pytest.raises(music.MusicError, match="complete"):
        music.write_bed(tmp_path, loop_start=0.2, loop_len=0.5, length=1)


def test_music_render_cache_does_not_repeat_head(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_head(mp, out_dir):
        calls.append(mp["key"])
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        _write_audio(folder / "head.wav", np.full((int(21 * 44100), 2), 0.04))
        _write_audio(folder / "hit.wav", np.full((int(1.6 * 44100), 2), 0.1))

    monkeypatch.setattr(music, "render_head", fake_head)
    report = music.render_music(PLAN, tmp_path)
    assert list(report) == [
        "key",
        "mood",
        "bpm",
        "beat",
        "bed_s",
        "intro_end_est",
        "head_s",
        "loop_start",
        "loop_len",
        "total_est",
        "hit_lead_s",
        "time_s",
    ]
    assert calls == [report["key"]]
    assert music.render_music(PLAN, tmp_path) == report
    assert calls == [report["key"]]
    assert len(music._validated_audio(tmp_path / "bed.wav")[0]) == int(51 * 44100)


def test_track_hits_fades_and_zero_tail(tmp_path: Path) -> None:
    rate = music.SAMPLE_RATE
    total = 4.0
    _write_audio(tmp_path / "bed.wav", np.full((int(6 * rate), 2), 0.15))
    _write_audio(tmp_path / "head.wav", np.full((int(6 * rate), 2), 0.15))
    _write_audio(tmp_path / "hit.wav", np.full((int(1.6 * rate), 2), 0.4))
    (tmp_path / "music.json").write_text(
        json.dumps(
            {
                "bed_s": 6,
                "loop_start": 1,
                "loop_len": 2,
            }
        )
    )
    timeline = {
        "bpm": 120,
        "beat": 0.5,
        "total": total,
        "cuts": [1.5, 2.5],
        "scenes": [{"start": 0}, {"start": 1.5}, {"start": 2.5}],
    }
    target = tmp_path / "final.wav"
    assert music.build_music_track(tmp_path, timeline, target) == target
    audio, _ = music._validated_audio(target)
    assert audio.shape == (int((total + 0.5) * rate), 2)
    assert abs(audio[0, 0]) <= 1 / 32767
    assert audio[int(0.025 * rate), 0] == pytest.approx(0.15, abs=0.001)
    for cut in timeline["cuts"]:
        begin = int((cut - 0.4) * rate)
        assert audio[begin + 500, 0] == pytest.approx(0.55, abs=0.001)
        sample_at = begin + int(0.7 * rate)
        current_s = sample_at / rate
        fade = min(1.0, (total - current_s) / 1.5)
        assert audio[sample_at, 0] == pytest.approx(0.15 * fade, abs=0.001)
    assert abs(audio[int(3.99 * rate), 0]) < 0.003
    assert np.all(audio[int(total * rate) :] == 0)
    assert np.max(np.abs(audio)) <= 1.0


def test_track_extends_bed_when_timeline_runs_long(tmp_path: Path) -> None:
    _write_audio(tmp_path / "head.wav", np.full((int(5 * 44100), 2), 0.1))
    _write_audio(tmp_path / "bed.wav", np.full((int(3 * 44100), 2), 0.1))
    _write_audio(tmp_path / "hit.wav", np.zeros((int(1.6 * 44100), 2)))
    (tmp_path / "music.json").write_text(
        json.dumps(
            {
                "bed_s": 3,
                "loop_start": 1,
                "loop_len": 1,
            }
        )
    )
    timeline = {
        "bpm": 120,
        "beat": 0.5,
        "total": 4,
        "cuts": [],
        "scenes": [{"start": 0}],
    }
    music.build_music_track(tmp_path, timeline, tmp_path / "track.wav")
    assert json.loads((tmp_path / "music.json").read_text())["bed_s"] == 6
    assert len(music._validated_audio(tmp_path / "bed.wav")[0]) == int(6 * 44100)


@pytest.mark.parametrize(
    "timeline",
    [
        {},
        {"bpm": float("nan"), "beat": 0.5, "total": 4, "scenes": [], "cuts": []},
        {"bpm": 120, "beat": 0.5, "total": float("inf"), "scenes": [], "cuts": []},
        {"bpm": 120, "beat": 0.5, "total": 4, "scenes": [{"start": 0}], "cuts": [5]},
        {"bpm": 120, "beat": 0.5, "total": 4, "scenes": [{"start": 0}], "cuts": ["x"]},
    ],
)
def test_invalid_timeline_rejected_before_file_io(tmp_path: Path, timeline) -> None:
    with pytest.raises(music.MusicError):
        music.build_music_track(tmp_path, timeline, tmp_path / "track.wav")


def test_html_is_offline_and_vendor_pin_is_untouched() -> None:
    html = music.HTML_FILE.read_bytes()
    vendor = music.TONE_FILE.read_bytes()
    assert html.startswith(b'<html><body><script src="vendor/Tone.js"></script><script>')
    assert html.endswith(b"</script></body></html>\n")
    assert b"http://" not in html and b"https://" not in html
    assert len(vendor) == 345500
    assert hashlib.sha256(vendor).hexdigest() == (
        "e290952fa43d9a7a780182a83c6fccf44d79cb7ae2cba102ef1f2b9d98124e22"
    )


def test_render_head_rejects_external_request_even_with_audio(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import sys
    import types

    state: dict[str, object] = {"closed": False, "aborted": False}

    class FakeRoute:
        request = types.SimpleNamespace(url="https://example.invalid/track")

        def abort(self) -> None:
            state["aborted"] = True

        def continue_(self) -> None:
            raise AssertionError("network was allowed")

    class FakePage:
        def set_default_timeout(self, value: int) -> None:
            assert value > 0

        def route(self, pattern: str, callback) -> None:
            assert pattern == "**/*"
            self.callback = callback

        def goto(self, url: str, **kwargs) -> None:
            assert url.startswith("file://")
            self.callback(FakeRoute())

        def evaluate(self, expression: str, arguments: object) -> str:
            return "aGVsbG8="

    class FakeBrowser:
        def new_page(self):
            return FakePage()

        def close(self):
            state["closed"] = True

    browser = FakeBrowser()

    class FakePlaywrightContext:
        def __enter__(self):
            return types.SimpleNamespace(
                chromium=types.SimpleNamespace(launch=lambda **kwargs: browser)
            )

        def __exit__(self, *args) -> None:
            pass

    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.sync_playwright = FakePlaywrightContext  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)

    with pytest.raises(music.MusicError, match="blocked 1 external"):
        music.render_head(music.music_plan(PLAN), tmp_path)
    assert state == {"closed": True, "aborted": True}
    assert not (tmp_path / "head.wav").exists()


@pytest.mark.parametrize("mood", ["upbeat", "chill", "epic"])
def test_optional_real_chromium_music(mood: str, tmp_path: Path) -> None:
    pytest.importorskip("playwright")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {type(exc).__name__}")

    plan = json.loads(json.dumps(PLAN))
    plan["music"]["mood"] = mood
    report = music.render_music(plan, tmp_path / mood)
    assert set(report) == {
        "key",
        "mood",
        "bpm",
        "beat",
        "bed_s",
        "intro_end_est",
        "head_s",
        "loop_start",
        "loop_len",
        "total_est",
        "hit_lead_s",
        "time_s",
    }
    for filename, seconds in [
        ("head.wav", report["head_s"] + 0.5),
        ("hit.wav", 1.6),
        ("bed.wav", report["bed_s"]),
    ]:
        path = tmp_path / mood / filename
        with wave.open(str(path)) as wav:
            assert wav.getnchannels() == 2
            assert wav.getframerate() == 44100
            assert wav.getsampwidth() == 2
            assert abs(wav.getnframes() / wav.getframerate() - seconds) <= 0.002
        values, _ = music._validated_audio(path)
        # The isolated 1.6 s accent uses intentionally lower gains than the bed.
        minimum_rms = 0.005 if filename == "hit.wav" else 0.01
        assert np.sqrt(np.mean(values**2)) > minimum_rms
        assert np.count_nonzero(np.abs(values) >= 0.999) < len(values) * 0.005
