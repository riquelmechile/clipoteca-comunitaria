"""Browser query encoder adapted from DTK v5 signing/base.py (Apache-2.0)."""

from collections.abc import Iterable

_BROWSER_ESCAPES = {" ": "%20", '"': "%22", "<": "%3C", ">": "%3E", "`": "%60", "#": "%23"}


def _browser_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in _BROWSER_ESCAPES:
            out.append(_BROWSER_ESCAPES[ch])
        elif " " < ch <= "~":
            out.append(ch)
        else:
            out.extend(f"%{byte:02X}" for byte in ch.encode("utf-8"))
    return "".join(out)


def encode_browser_query(pairs: Iterable[tuple[str, str]]) -> str:
    return "&".join(
        f"{_browser_escape(str(key))}={_browser_escape(str(value))}" for key, value in pairs
    )
