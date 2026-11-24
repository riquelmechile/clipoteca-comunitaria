"""M34 workflow proposal: validate the promoted contract without executing Actions."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow():
    actual = ROOT / ".github/workflows/render.yml"
    if actual.exists():
        candidate = yaml.load(actual.read_text(), Loader=yaml.BaseLoader)
        if "index_clips" in candidate["on"]["workflow_dispatch"]["inputs"]:
            return candidate
    for name in ("propuestas/render.workflow.yml", "propuestas/github/workflows/render.yml"):
        proposal = ROOT / name
        if proposal.is_file():
            return yaml.load(proposal.read_text(), Loader=yaml.BaseLoader)
    raise AssertionError("missing render proposal")


def test_render_workflow_delivery_contract():
    spec = _workflow()
    inputs = spec["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"script_json", "request_id", "index_clips"}
    assert inputs["request_id"]["required"] == "true"
    assert inputs["index_clips"]["default"] == "true"
    assert spec["run-name"] == "render ${{ inputs.request_id }}"
    jobs = spec["jobs"]
    assert "publish" not in jobs
    assert spec["permissions"] == {"contents": "read"}
    assert jobs["index"]["permissions"] == {"contents": "write"}
    assert all("permissions" not in job for key, job in jobs.items() if key != "index")
    assert jobs["index"]["concurrency"] == {
        "group": "clipoteca-catalog",
        "cancel-in-progress": "false",
    }
    assert jobs["index"]["needs"] == "scene"
    steps = jobs["assemble"]["steps"]
    uploads = {
        step["with"]["name"]: step["with"]
        for step in steps
        if "upload-artifact@" in step.get("uses", "")
    }
    video = uploads["video-${{ inputs.request_id }}"]
    assert video["path"] == "work/video.mp4"
    assert video["compression-level"] == "0"
    assert video["retention-days"] == "7"
    delivery = uploads["entrega-${{ inputs.request_id }}"]
    assert set(delivery["path"].split()) == {
        "work/entrega.json",
        "work/qa_report.json",
        "work/render.json",
        "work/timings.json",
    }
    assert delivery["retention-days"] == "7"
    for job in jobs.values():
        for step in job["steps"]:
            assert "inputs.script_json" not in step.get("run", "")
            if "actions/checkout@" in step.get("uses", ""):
                assert step["with"]["persist-credentials"] == "false"
    assert any("A-Za-z0-9_-]{8,64}" in step.get("run", "") for step in jobs["plan"]["steps"])


def test_pinned_ffmpeg_installer_and_scene_retry():
    actual = ROOT / ".github/actions/install-ffmpeg/action.yml"
    proposal = ROOT / "propuestas/install-ffmpeg.action.yml"
    installer = actual.read_text() if actual.is_file() else ""
    if "b6.1.1/ffmpeg-linux-x64" not in installer:
        installer = proposal.read_text()
    spec = yaml.load(installer, Loader=yaml.BaseLoader)
    assert spec["runs"]["using"] == "composite"
    assert "seconds" in spec["outputs"]
    assert "sha256sum -c --strict" in installer
    assert "b6.1.1/ffmpeg-linux-x64" in installer
    assert "b6.1.1/ffprobe-linux-x64" in installer
    assert "apt-get install" in installer
    assert "GITHUB_PATH" in installer
    jobs = _workflow()["jobs"]
    commands = "\n".join(step.get("run", "") for step in jobs["scene"]["steps"])
    assert "--max-results 8 --spread-queries --timeout 600" in commands
    assert "--max-results 2 --spread-queries --simple --timeout 180" in commands
    assert "install_ffmpeg" in commands
    assert "import-retry" in commands
