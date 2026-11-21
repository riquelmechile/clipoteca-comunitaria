"""Exactly fifteen discoverable sources, explicit auth gates and truthful scopes."""

import json

from clipoteca.cli import main
from clipoteca.sources import SOURCES, list_sources


NAMES = {
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


def test_list_sources_15_unique_and_all_have_language_matrix():
    rows = list_sources(env={})
    assert len(rows) == len(SOURCES) == 15
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
    assert first["count"] == 15
    assert set(x["name"] for x in first["sources"]) == NAMES
    assert main(["list-sources"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in first["sources"]] == [r["name"] for r in second["sources"]]
