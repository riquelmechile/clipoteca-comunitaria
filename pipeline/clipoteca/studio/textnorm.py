"""Deterministic normalization for voice quality checks."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence

_NUMBER_WORDS = {
    3: "tres",
    4: "cuatro",
    5: "cinco",
    6: "seis",
    7: "siete",
    8: "ocho",
    10: "diez",
    12: "doce",
}
DEFAULT_LEXICON = {
    **{f"RS{i}": f"erre ese {_NUMBER_WORDS[i]}" for i in range(3, 8)},
    **{f"V{i}": f"ve {_NUMBER_WORDS[i]}" for i in (6, 8, 10, 12)},
    "DTM": "de te eme",
    "TDI": "te de i",
    "4x4": "cuatro por cuatro",
    "km/h": "kilometros por hora",
}


def apply_lexicon(text: str, lexicon: Mapping[str, str] | None = None) -> str:
    """Replace whole spoken tokens, preferring longer lexicon keys."""
    values = DEFAULT_LEXICON if lexicon is None else lexicon
    result = text
    for key in sorted(values, key=len, reverse=True):
        pattern = rf"(?<![\w]){re.escape(key)}(?![\w])"
        replacement = values[key]
        # A callable keeps the replacement literal: backslashes in a script
        # lexicon must not be read as group references.
        result = re.sub(pattern, lambda _match: replacement, result, flags=re.IGNORECASE)
    return result


def norm_words(value: str, lang: str = "es") -> list[str]:
    """Return accent-insensitive words suitable for WER."""
    value = value.lower().replace("%", " por ciento ")
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"(?<=\d)[.,](?=\d{3}(?:\D|$))", "", value)
    try:
        from num2words import num2words
    except ImportError:
        num2words = None
    if num2words is not None:
        value = re.sub(r"\d+", lambda m: str(num2words(int(m.group()), lang=lang)), value)
    value = value.replace("ñ", "\0")
    value = "".join(
        char for char in unicodedata.normalize("NFD", value) if unicodedata.category(char) != "Mn"
    ).replace("\0", "ñ")
    return re.sub(r"[^a-z0-9ñ ]+", " ", value).split()


def _distance(left: Sequence[object], right: Sequence[object]) -> int:
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        new = [i]
        for j, b in enumerate(right, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = new
    return row[-1]


def wer(ref_words: Sequence[str], hyp_words: Sequence[str]) -> float:
    """Word error rate; an empty reference is defined as zero."""
    return _distance(ref_words, hyp_words) / len(ref_words) if ref_words else 0.0
