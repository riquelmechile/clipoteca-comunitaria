"""Local Python-only Douyin/TikTok signing, Apache-2.0 and MIT; see NOTICE."""

import hashlib
import time
from urllib.parse import urlencode

from .abogus import ABogus
from .xbogus import XBogus
from .sm3 import sm3_hash, sm3_hexdigest
from .tiktok_sign import sign as sign_tiktok
from .tokens import gen_false_ms_token, TTWID_REGISTER_URL

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SEC_SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"


def sign_douyin(params: dict[str, str]) -> dict[str, str]:
    """Produce a_bogus and X-Bogus over the current encoded webapp parameters."""
    original = urlencode(params)
    bogus = ABogus(BROWSER_UA).get_value(original)
    with_bogus = {**params, "a_bogus": bogus}
    query = urlencode(with_bogus)
    return {**with_bogus, "X-Bogus": XBogus(BROWSER_UA).sign(query)}


def secsdk_sign(params: dict[str, str], uifid: str, *, timestamp: int | None = None):
    """Port of Youhai020616/douyin DouyinAPIClient._secsdk_sign (MIT)."""
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    covered = {**params, "uifid": uifid, "timestamp": stamp}
    encoded = urlencode(covered)
    signature = hashlib.md5(f"{uifid}_{stamp}_{_SEC_SALT}_{encoded}".encode()).hexdigest()
    return {**covered, "x-secsdk-web-signature": signature}


__all__ = [
    "ABogus",
    "XBogus",
    "sm3_hash",
    "sm3_hexdigest",
    "sign_tiktok",
    "gen_false_ms_token",
    "TTWID_REGISTER_URL",
    "BROWSER_UA",
    "secsdk_sign",
    "sign_douyin",
]
