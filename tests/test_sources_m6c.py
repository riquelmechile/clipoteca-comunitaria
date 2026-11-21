"""Discoverable sources, explicit auth gates and truthful scopes."""

import json

from clipoteca.cli import main
from clipoteca.sources import SOURCES, list_sources


NAMES = {
    "library_of_congress",
    "coverr",
    "europeana",
    "nara",
    "dpla",
    "smithsonian",
    "pexels",
    "pixabay",
    "wikimedia_commons",
    "internet_archive",
    "nasa",
    "youtube",
    "rutube",
    "peertube",
    "odysee",
    "bilibili",
    "vk_video",
    "douyin",
    "tiktok",
    "instagram",
    "snapchat",
}


def test_exported_source_snapshot_matches_code(tmp_path):
    from pathlib import Path

    expected = list_sources(env={})
    committed = Path(__file__).resolve().parents[1] / "data/sources.json"
    # Actions rewrites the public credential-presence snapshot with real
    # boolean availability. Stable capabilities must still match the code,
    # but that runtime presence must not be mistaken for an offline fixture.
    live = json.loads(committed.read_text(encoding="utf-8"))
    assert len(live) == len(expected)
    by_name = {row["name"]: row for row in live}
    assert set(by_name) == {row["name"] for row in expected}
    for row in expected:
        observed = by_name[row["name"]]
        assert isinstance(observed["credentials_present"], bool)
        assert {
            key: value
            for key, value in observed.items()
            if key not in {"status", "credentials_present", "download_supported"}
        } == {
            key: value
            for key, value in row.items()
            if key
            not in {
                "status",
                "credentials_present",
                "download_supported",
                "requires",
                "missing_credential_names",
            }
        }
    assert "VERY-SECRET" not in json.dumps(live)
    destination = tmp_path / "sources.json"
    assert main(["export-sources", "--output", str(destination)]) == 0
    assert json.loads(destination.read_text(encoding="utf-8")) == expected


def test_list_sources_21_unique_and_all_have_language_matrix():
    rows = list_sources(env={})
    assert len(rows) == len(SOURCES) == 21
    assert {row["name"] for row in rows} == NAMES
    assert all(
        set(row["accepts_text_languages"]).issubset({"es", "en", "zh", "ru"}) for row in rows
    )
    assert all(not row["languages_semantically_verified"] for row in rows)
    assert all(row["status"] and row["scope"] for row in rows)
    assert all("token_value" not in row for row in rows)


def test_auth_required_when_missing_and_no_false_global_search_claims():
    rows = {row["name"]: row for row in list_sources(env={})}
    for key in (
        "pexels",
        "pixabay",
        "coverr",
        "europeana",
        "nara",
        "dpla",
        "smithsonian",
        "youtube",
        "vk_video",
        "douyin",
        "tiktok",
        "instagram",
        "snapchat",
    ):
        assert rows[key]["status"] == "AUTH_REQUIRED"
    for key in ("douyin", "tiktok", "instagram", "snapchat"):
        assert "authorized_" in rows[key]["scope"]
        assert not rows[key]["download_supported"]
        assert rows[key]["support_status"] == "SUPPORTED_SEARCH_ONLY"


def test_env_values_never_revealed_even_with_fake_keys():
    env = {
        "PEXELS_API_KEY": "VERY-SECRET",
        "PIXABAY_API_KEY": "VERY-SECRET",
        "COVERR_API_KEY": "VERY-SECRET",
        "EUROPEANA_API_KEY": "VERY-SECRET",
        "NARA_API_KEY": "VERY-SECRET",
        "DPLA_API_KEY": "VERY-SECRET",
        "SMITHSONIAN_API_KEY": "VERY-SECRET",
        "YOUTUBE_API_KEY": "VERY-SECRET",
        "VK_ACCESS_TOKEN": "VERY-SECRET",
        "DOUYIN_ACCESS_TOKEN": "VERY-SECRET",
        "TIKTOK_ACCESS_TOKEN": "VERY-SECRET",
        "INSTAGRAM_ACCESS_TOKEN": "VERY-SECRET",
        "INSTAGRAM_USER_ID": "123456789",
        "SNAPCHAT_ACCESS_TOKEN": "VERY-SECRET",
        "SNAPCHAT_PROFILE_ID": "76da494b-76bc-4bbb-bb27-c5a66fb0d1ab",
    }
    rows = list_sources(env=env)
    assert "VERY-SECRET" not in json.dumps(rows)
    assert {entry["name"]: entry["status"] for entry in rows}["tiktok"] == "SUPPORTED_SEARCH_ONLY"


def test_cli_list_sources_json_and_alias(capsys):
    assert main(["list_sources"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["count"] == 21
    assert set(x["name"] for x in first["sources"]) == NAMES
    assert main(["list-sources"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in first["sources"]] == [r["name"] for r in second["sources"]]
