"""TikTok Web search parameter port from DTK v5 platforms/tiktok/params.py.

Copyright Evil0ctal/Douyin_TikTok_Download_API contributors. Apache-2.0.
Only base_params/search_posts_params required by M19 are included.
"""

import re
import secrets


SEARCH_CURSOR = re.compile(r"(\d{1,9})(?::([0-9A-Za-z]{1,64}))?")
WEB_ORIGIN = "https://www.tiktok.com/"


def base_params(*, device_id: str | None = None) -> dict[str, str]:
    return {
        "aid": "1988",
        "app_language": "en",
        "app_name": "tiktok_web",
        "browser_language": "en-US",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "browser_platform": "Win32",
        "browser_version": "5.0 (Windows)",
        "channel": "tiktok_web",
        "cookie_enabled": "true",
        "device_id": device_id or str(secrets.randbelow(9 * 10**18) + 10**18),
        "device_platform": "web_pc",
        "focus_state": "true",
        "from_page": "user",
        "history_len": "4",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language": "en",
        "os": "windows",
        "priority_region": "US",
        "referer": "",
        "region": "US",
        "root_referer": WEB_ORIGIN,
        "screen_height": "1080",
        "screen_width": "1920",
        "tz_name": "America/Los_Angeles",
        "webcast_language": "en",
    }


def search_posts_params(
    *,
    keyword: str,
    cursor: str | None = None,
    count: int = 20,
    device_id: str | None = None,
) -> dict[str, str]:
    if not keyword.strip():
        raise ValueError("a search needs a keyword")
    params = {
        **base_params(device_id=device_id),
        "keyword": keyword.strip(),
        "offset": "0",
        "count": str(count),
        "search_source": "normal_search",
    }
    if cursor:
        match = SEARCH_CURSOR.fullmatch(cursor.strip())
        if match is None:
            raise ValueError("invalid TikTok search cursor")
        offset, search_id = match.groups()
        params["offset"] = offset
        if search_id:
            params["search_id"] = search_id
    return params
