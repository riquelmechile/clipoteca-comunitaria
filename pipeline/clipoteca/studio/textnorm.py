"""Deterministic text normalization and Spanish spoken numbers."""

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

_UNITS = (
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
    "veinte",
    "veintiuno",
    "veintidós",
    "veintitrés",
    "veinticuatro",
    "veinticinco",
    "veintiséis",
    "veintisiete",
    "veintiocho",
    "veintinueve",
)
_TENS = ("", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa")
_HUNDREDS = (
    "",
    "ciento",
    "doscientos",
    "trescientos",
    "cuatrocientos",
    "quinientos",
    "seiscientos",
    "setecientos",
    "ochocientos",
    "novecientos",
)
_NUMBERS = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*(?: ?%)?(?![\w])")


def apply_lexicon(text: str, lexicon: Mapping[str, str] | None = None) -> str:
    """Replace case-sensitive whole tokens, keeping replacements literal."""
    values = DEFAULT_LEXICON if lexicon is None else lexicon
    result = text
    for key in sorted(values, key=len, reverse=True):
        pattern = rf"(?<![\w]){re.escape(key)}(?![\w])"
        replacement = values[key]
        result = re.sub(pattern, lambda _match: replacement, result)
    return result


def _under_thousand(value: int) -> str:
    if value < 30:
        return _UNITS[value]
    if value < 100:
        ten, unit = divmod(value, 10)
        return _TENS[ten] + (" y " + _UNITS[unit] if unit else "")
    if value == 100:
        return "cien"
    hundred, rest = divmod(value, 100)
    return _HUNDREDS[hundred] + (" " + _under_thousand(rest) if rest else "")


def _apocope(value: str) -> str:
    """Use un/veintiún when a cardinal precedes mil or millones."""
    if value.endswith("veintiuno"):
        return value[:-9] + "veintiún"
    if value.endswith(" y uno"):
        return value[:-6] + " y un"
    if value.endswith("uno"):
        return value[:-3] + "un"
    return value


def _under_million(value: int) -> str:
    if value < 1000:
        return _under_thousand(value)
    thousands, rest = divmod(value, 1000)
    before = "mil" if thousands == 1 else _apocope(_under_thousand(thousands)) + " mil"
    return before + (" " + _under_thousand(rest) if rest else "")


def _cardinal(value: int) -> str:
    if value < 1_000_000:
        return _under_million(value)
    millions, rest = divmod(value, 1_000_000)
    if millions == 1:
        before = "un millón"
    else:
        before = _apocope(_under_million(millions)) + " millones"
    return before + (" " + _under_million(rest) if rest else "")


def _spell_number(value: str) -> str:
    percentage = value.endswith("%")
    original = value[:-1].rstrip() if percentage else value
    if original.count(",") > 1 or ("," in original and "." in original):
        return value
    if "," in original:
        integer, fraction = original.split(",")
        if not integer.isdecimal() or not fraction.isdecimal():
            return value
    elif "." in original:
        groups = original.split(".")
        if len(groups) > 2 or len(groups[-1]) == 3:
            if not all(len(group) == 3 for group in groups[1:]):
                return value
            if len(groups[0]) > 3 or not all(group.isdecimal() for group in groups):
                return value
            integer, fraction = "".join(groups), ""
        else:
            integer, fraction = groups[0], groups[-1]
    else:
        integer, fraction = original, ""
    if not integer.isdecimal() or (fraction and not fraction.isdecimal()):
        return value
    number = int(integer)
    if number > 999_999_999_999:
        return value
    words = _cardinal(number)
    if fraction:
        fractional = int(fraction)
        if fractional > 999_999_999_999:
            return value
        words += " coma " + _cardinal(fractional)
    return words + (" por ciento" if percentage else "")


def spell_numbers(text: str, lang: str = "es") -> str:
    """Expand Spanish numbers to spoken words; preserve unrelated text exactly.

    Dot followed by a group of three digits denotes thousands; a final dot
    followed by one or two digits denotes decimals. Comma denotes decimals.
    Unrecognised formats and values above 999,999,999,999 remain untouched.
    """
    if lang != "es":
        return text
    return _NUMBERS.sub(lambda match: _spell_number(match.group()), text)


def norm_words(value: str, lang: str = "es") -> list[str]:
    """Accent-insensitive WER tokens, using the same numeric expansion as TTS."""
    value = spell_numbers(value, lang).lower().replace("%", " por ciento ")
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
