"""M9 Issue triage is strictly read-only with respect to untrusted media URLs."""

import runpy
from pathlib import Path

triage = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts" / "review_contribution.py")
)
classify = triage["classify"]
issue_source_url = triage["issue_source_url"]
safe_url = triage["safe_url"]


def test_safe_public_url_only():
    assert safe_url("https://videos.example.org/watch/123")
    for value in [
        "http://example.org",
        "https://127.0.0.1/video",
        "https://10.1.2.3/video",
        "https://localhost",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://user:password@example.org/file",
        "https://[::1]/video",
    ]:
        assert not safe_url(value)


def test_issue_form_is_data_not_commands():
    body = (
        "### URL pública del vídeo\n\nhttps://videos.example.org/watch/123\n\n### Command\nrm -rf /"
    )
    assert issue_source_url(body) == "https://videos.example.org/watch/123"
    assert classify(body) == "clipoteca-pending-review"
    assert classify("### URL pública del vídeo\n\nhttps://127.0.0.1/") == "clipoteca-invalid-url"
    assert classify("### Otro título\n\nhttps://videos.example.org") == "clipoteca-invalid-url"
