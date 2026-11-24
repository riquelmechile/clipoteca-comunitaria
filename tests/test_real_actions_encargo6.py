"""Regressions for the actual Clipoteca Actions runs from 2026-11-20.

No external network, secrets or real GitHub writes; the push simulation uses
an ephemeral local bare Git repository.
"""

from __future__ import annotations

import asyncio
import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from clipoteca import cli
from clipoteca.engine import SearchEngine
from clipoteca.ingest import IngestReport
from clipoteca.metadata import localize_candidate
from clipoteca.providers.base import Candidate, SupportStatus
from clipoteca.providers.peertube import PeerTube
from clipoteca.relocalize import relocalize_catalog
from clipoteca.translate.argos import TranslationFailed, translate_pair, translate_text

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
probe = runpy.run_path(str(SCRIPTS / "smoke_sources.py"))["probe"]
push_catalog_with_retry = runpy.run_path(str(SCRIPTS / "push_catalog_rebase.py"))[
    "push_catalog_with_retry"
]


@pytest.mark.asyncio
async def test_peertube_empty_actions_variable_uses_documented_safe_defaults(monkeypatch):
    monkeypatch.setenv("PEERTUBE_ALLOWED_INSTANCES", "")
    adapter = PeerTube(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []}))
        )
    )
    try:
        assert "pt.na4.eu" in adapter.instances
        assert "tilvids.com" in adapter.instances
        assert len(adapter.instances) >= 4
    finally:
        await adapter.client.aclose()


class MisconfiguredPeer:
    accepts_text_languages = ("es", "en", "zh", "ru")

    def __init__(self):
        raise ValueError("private-secret-in-exception")


class WorkingOdysee:
    accepts_text_languages = ("es", "en", "zh", "ru")
    support_status = SupportStatus.SUPPORTED_SEARCH_DOWNLOAD
    name = "odysee"

    async def search(self, term, language, limits):
        return []


@pytest.mark.asyncio
async def test_smoke_bad_provider_constructor_isolated_without_secret_leak():
    bad = SimpleNamespace(name="peertube", adapter=MisconfiguredPeer, requires=())
    good = SimpleNamespace(name="odysee", adapter=WorkingOdysee, requires=())
    failed, okay = await asyncio.gather(probe(bad, "es"), probe(good, "en"))
    assert failed["status"] == "UNAVAILABLE"
    assert failed["error_type"] == "ValueError"
    assert "private-secret" not in json.dumps(failed)
    assert okay["status"] == str(SupportStatus.SUPPORTED_SEARCH_DOWNLOAD)


def test_cli_bad_provider_constructor_isolated_from_healthy_ingest(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "SOURCES",
        (
            SimpleNamespace(name="peertube", adapter=MisconfiguredPeer),
            SimpleNamespace(name="odysee", adapter=WorkingOdysee),
        ),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dummy/clipoteca")

    async def run_fake_ingest(query, adapters, **kwargs):
        result = await SearchEngine(adapters).search(query)
        failed = [entry for entry in result.executions if entry.provider == "peertube"]
        healthy = [entry for entry in result.executions if entry.provider == "odysee"]
        assert failed and all(x.status == SupportStatus.UNAVAILABLE for x in failed)
        assert all(x.error == "adapter_init_ValueError" for x in failed)
        assert healthy and all(x.status == WorkingOdysee.support_status for x in healthy)
        return IngestReport("a" * 64, "no_results", (), ())

    monkeypatch.setattr(cli, "ingest", run_fake_ingest)
    assert cli.main(["ingest", "--query", "lluvia", "--providers", "peertube,odysee"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "no_results"


@pytest.mark.parametrize(
    "value,reason",
    [
        ("Autopistas", "unchanged_translation"),
        ("mainstre" * 70, "disproportionate_translation"),
        ("abcde" * 4, "repeated_translation"),
    ],
)
def test_argos_rejects_real_degenerate_and_unchanged_outputs(value, reason):
    with pytest.raises(TranslationFailed, match=reason):
        translate_pair("Autopistas", "es", "en", translator=lambda *_: value)


@pytest.mark.parametrize(
    ("source", "translated", "src", "dst"),
    [
        ("Olas en la playa", "Waves on the beach", "es", "en"),
        (
            "Waves at Hikkaduwa beach, Sri Lanka",
            "Olas en la playa de Hikkaduwa, Sri Lanka",
            "en",
            "es",
        ),
        (
            "Prueba E2E de M9. (reintento tras corrección)",
            "M9 end-to-end test (retry after correction).",
            "es",
            "en",
        ),
        (
            "A person walking down a sidewalk in the rain",
            "Una persona camina por la acera bajo la lluvia.",
            "en",
            "es",
        ),
        (
            "Video corto de las olas del mar en la playa de Sri Lanka.",
            "A short video of ocean waves on the beach in Sri Lanka.",
            "es",
            "en",
        ),
        (
            "Snow falling on a quiet street at night.",
            "Nieve cayendo sobre una calle tranquila de noche.",
            "en",
            "es",
        ),
    ],
)
def test_argos_size_guard_accepts_realistic_es_en_texts(source, translated, src, dst):
    assert translate_pair(source, src, dst, translator=lambda *_: translated) == translated


@pytest.mark.parametrize(
    ("source", "output", "reason"),
    [
        ("olas", "word " * 100, "disproportionate_translation"),
        ("Olas en la playa", "mainstre" * 18, "repeated_translation"),
        ("Autopistas", "mainstre" * 70, "disproportionate_translation"),
    ],
)
def test_argos_size_guard_rejects_degenerate_repetitions(source, output, reason):
    with pytest.raises(TranslationFailed, match=reason):
        translate_pair(source, "es", "en", translator=lambda *_: output)


def test_curated_fallback_only_for_corrupt_model_output_not_missing_models(monkeypatch):
    assert (
        translate_text("Autopistas", "es", "en", translator=lambda text, src, dst: "Autopistas")
        == "Highways"
    )
    with pytest.raises(TranslationFailed):
        translate_text("texto desconocido", "es", "en", translator=lambda *_: "mainstre" * 70)
    monkeypatch.delenv("ARGOS_MODEL_PINS_JSON", raising=False)
    with pytest.raises(TranslationFailed, match="model_pins_not_configured"):
        translate_text("Autopistas", "es", "en")


def test_relocalize_clip_000002_repairs_real_source_and_drops_stale_argos_garbage(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "data/clips.json"
    before = json.loads(fixture.read_text(encoding="utf-8"))
    # The real catalogue was already repaired by relocalize; guard that clean state.
    assert all("mainstremain" not in tag for tag in before[1]["tags"]["en"])
    target = tmp_path / "clips.json"
    target.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    relocalize_catalog(
        target,
        ["clip-000002"],
        index_root=tmp_path / "index",
        language_overrides={"clip-000002": "es"},
        translator=lambda text, src, dst: text,  # simulate the bad Argos identity output
    )
    after = json.loads(target.read_text(encoding="utf-8"))
    assert after[1]["title"] == {"es": "Autopistas", "en": "Highways"}
    assert after[1]["source"]["source_language"] == "es"
    assert "highways" in [tag.casefold() for tag in after[1]["tags"]["en"]]
    # Rebuilding from the factual title removes a stale, unrelated rain tag.
    assert "rain" not in [tag.casefold() for tag in after[1]["tags"]["en"]]
    assert not any("mainstream" in tag for tag in after[1]["tags"]["en"])
    assert after[1]["storage"] == before[1]["storage"]
    assert after[2] == before[2]


def test_relocalize_clip_000003_keeps_english_source_and_correct_spanish_direction(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "data/clips.json"
    before = json.loads(fixture.read_text(encoding="utf-8"))
    target = tmp_path / "clips.json"
    target.write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    relocalize_catalog(
        target,
        ["clip-000003"],
        index_root=tmp_path / "index",
        translator=lambda text, src, dst: (
            "Un hombre caminando por una acera en la lluvia" if dst == "es" else text
        ),
    )
    after = json.loads(target.read_text(encoding="utf-8"))
    assert all("mainstremain" not in tag for tag in before[2]["tags"]["en"])
    assert after[2]["title"]["en"] == "A man walking down a sidewalk in the rain"
    assert after[2]["title"]["es"] == "Un hombre caminando por una acera en la lluvia"
    assert after[2]["source"]["source_language"] == "en"
    assert all("mainstream" not in word for word in after[2]["tags"]["en"])
    assert len(set(word.casefold() for word in after[2]["tags"]["en"])) == len(
        after[2]["tags"]["en"]
    )
    assert after[2]["storage"] == before[2]["storage"]


def test_ignore_unknown_discovery_terms_instead_of_inventing_text():
    candidate = Candidate(
        "pexels", "4", "palabra desconocida", "es", "https://example.org/v", "Autopistas", "es"
    )
    translated = localize_candidate(
        candidate,
        translator=lambda text, src, dst: "mainstre" * 70 if text == "palabra" else "Highways",
    )
    assert translated.tags == {"es": ["Autopistas"], "en": ["Highways"]}


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15, check=True
    )
    return proc.stdout.strip()


def _commit(repo: Path, path: str, text: str, message: str) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-m", message, cwd=repo)


def test_relocalize_push_rebases_against_concurrent_source_snapshot(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    writer = tmp_path / "writer"
    other = tmp_path / "other"
    _git("init", "--bare", "-b", "main", str(remote))
    _git("init", "-b", "main", str(seed))
    _git("config", "user.name", "test", cwd=seed)
    _git("config", "user.email", "test@example.invalid", cwd=seed)
    _commit(seed, "data/clips.json", "[]\n", "init catalog")
    _git("remote", "add", "origin", str(remote), cwd=seed)
    _git("push", "origin", "main", cwd=seed)
    for dest in (writer, other):
        _git("clone", "--branch", "main", str(remote), str(dest))
        _git("config", "user.name", "test", cwd=dest)
        _git("config", "user.email", "test@example.invalid", cwd=dest)
    _commit(writer, "data/clips.json", '[{"id":"clip-000002"}]\n', "relocalize")
    _commit(other, "data/sources.json", '[{"name":"peertube"}]\n', "snapshot")
    _git("push", "origin", "main", cwd=other)
    monkeypatch.chdir(writer)
    assert push_catalog_with_retry() == 1
    assert _git("show", "main:data/sources.json", cwd=other) == '[{"name":"peertube"}]'
    _git("pull", "--ff-only", "origin", "main", cwd=other)
    assert _git("show", "HEAD:data/clips.json", cwd=other) == '[{"id":"clip-000002"}]'
    assert _git("show", "HEAD:data/sources.json", cwd=other) == '[{"name":"peertube"}]'


def test_relocalize_workflow_calls_nonforced_bounded_rebase():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/relocalize.yml"
    script = Path(__file__).resolve().parents[1] / "scripts/push_catalog_rebase.py"
    assert "scripts/push_catalog_rebase.py" in workflow.read_text(encoding="utf-8")
    assert "git push origin HEAD:main" not in workflow.read_text(encoding="utf-8")
    source = script.read_text(encoding="utf-8")
    assert "pull" in source and "--rebase" in source
    assert "--force" not in source


def test_push_retry_is_bounded_after_non_fast_forward(monkeypatch):
    calls = []
    sequence = [0, 1, 0, 0]  # pull, rejected push, fresh pull, accepted push

    def fake_git(*args):
        calls.append(args)
        return SimpleNamespace(returncode=sequence.pop(0))

    monkeypatch.setitem(push_catalog_with_retry.__globals__, "_git", fake_git)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert push_catalog_with_retry() == 2
    assert calls == [
        ("pull", "--rebase", "origin", "main"),
        ("push", "origin", "HEAD:main"),
        ("pull", "--rebase", "origin", "main"),
        ("push", "origin", "HEAD:main"),
    ]
