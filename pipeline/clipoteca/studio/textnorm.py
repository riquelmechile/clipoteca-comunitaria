"""Deterministic text normalization and Spanish spoken numbers."""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Mapping, Sequence

DEFAULT_LEXICON = {
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
# Deliberately closed list. The singular/plural forms are both supported.
_FEMININE = {
    "persona",
    "hora",
    "vez",
    "semana",
    "tonelada",
    "milla",
    "hectárea",
    "especie",
    "libra",
    "caloría",
    "página",
    "ciudad",
    "mujer",
    "niña",
    "casa",
    "cosa",
    "parte",
    "vida",
    "isla",
    "lengua",
    "estrella",
    "galaxia",
    "noche",
    "personas",
    "horas",
    "veces",
    "semanas",
    "toneladas",
    "millas",
    "hectáreas",
    "especies",
    "libras",
    "calorías",
    "páginas",
    "ciudades",
    "mujeres",
    "niñas",
    "casas",
    "cosas",
    "partes",
    "vidas",
    "islas",
    "lenguas",
    "estrellas",
    "galaxias",
    "noches",
}
# Words that clearly do not introduce a noun (keep the uncontracted cardinal).
_NON_NOUNS = {
    "de",
    "del",
    "a",
    "al",
    "en",
    "por",
    "para",
    "con",
    "sin",
    "sobre",
    "entre",
    "y",
    "o",
    "u",
    "que",
    "como",
    "cuando",
    "donde",
    "si",
    "es",
    "son",
    "fue",
    "ser",
    "era",
    "fueron",
    "está",
    "están",
    "hay",
}
_ORDINAL_M = (
    "",
    "primero",
    "segundo",
    "tercero",
    "cuarto",
    "quinto",
    "sexto",
    "séptimo",
    "octavo",
    "noveno",
    "décimo",
)
_ORDINAL_F = (
    "",
    "primera",
    "segunda",
    "tercera",
    "cuarta",
    "quinta",
    "sexta",
    "séptima",
    "octava",
    "novena",
    "décima",
)
_MONTHS = (
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
# A single ordered matcher prevents a later numeric pass from partially rewriting
# dates, ordinal suffixes, fractions or clock notation.
_TOKEN = re.compile(
    r"(?<!\w)(?:"
    r"(?P<usd>US\$\s*\d+(?:[.,]\d+)*|USD\s+\d+(?:[.,]\d+)*)"
    r"|(?P<clp>\$\s*\d+(?:[.,]\d+)*)"
    r"|(?P<eur_pre>€\s*\d+(?:[.,]\d+)*)"
    r"|(?P<date>\d{1,2}/\d{1,2}/\d{4})"
    r"|(?P<time>\d{1,2}:\d{2})"
    r"|(?P<ordinal>(?:10|[1-9])(?:er|º|ª))"
    r"|(?P<fraction>\d+/\d+)"
    r"|(?P<eur_post>\d+(?:[.,]\d+)*\s+€)"
    r"|(?P<negative>-\d+(?:[.,]\d+)*)"
    r"|(?P<number>\d+(?:[.,]\d+)*(?:\s?%)?)"
    r")(?!\w)"
)


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


def _feminine_one(words: str) -> str:
    if words.endswith("veintiuno"):
        return words[:-9] + "veintiuna"
    if words.endswith(" y uno"):
        return words[:-6] + " y una"
    if words.endswith("uno"):
        return words[:-3] + "una"
    return words


def _following_word(text: str, end: int) -> str | None:
    # A punctuation mark stops the apocope, except whitespace before a noun.
    match = re.match(r"\s+([^\W\d_]+)\b", text[end:], re.UNICODE)
    if not match:
        return None
    value = match.group(1).casefold()
    return value if value not in _NON_NOUNS else None


def _spell_number(value: str, next_word: str | None = None) -> str:
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
        if len(fraction) >= 3 or fraction.startswith("0"):
            spoken = " ".join(_UNITS[int(digit)] for digit in fraction)
        else:
            spoken = _cardinal(int(fraction))
        words += " coma " + spoken
    elif not percentage and number % 10 == 1 and number % 100 != 11 and next_word:
        # Do not contract an isolated cardinal or one before punctuation.
        words = _feminine_one(words) if next_word in _FEMININE else _apocope(words)
    return words + (" por ciento" if percentage else "")


def _protected_identifier(text: str, start: int, end: int) -> bool:
    # The numeric fragment of clip-000023, H264 and v2.0.6 is not prose.
    if start >= 2 and text[start - 1] == "-" and text[start - 2].isalnum():
        return True
    if end < len(text) and re.match(r"-[0-9]", text[end:]):
        return True
    if (
        start >= 1
        and text[start - 1] == "/"
        and re.search(r"[A-Za-z][A-Za-z0-9_]*/$", text[:start])
    ):
        return True
    if start >= 1 and text[start - 1] == ".":
        prefix = re.search(r"[A-Za-z_]\w*(?:\.\d+)*\.$", text[:start])
        if prefix:
            return True
    return False


def _token_words(match: re.Match[str], text: str) -> str:
    raw = match.group()
    if _protected_identifier(text, match.start(), match.end()):
        return raw
    kind = match.lastgroup
    if kind == "date":
        day, month, year = (int(x) for x in raw.split("/"))
        if not 1 <= month <= 12 or not 1 <= day <= calendar.monthrange(year, month)[1]:
            return raw
        return f"{_cardinal(day)} de {_MONTHS[month]} de {_cardinal(year)}"
    if kind == "time":
        hour, minute = (int(x) for x in raw.split(":"))
        if hour > 23 or minute > 59:
            return raw
        if minute == 0:
            return f"{_cardinal(hour)} en punto"
        if minute < 10:
            return f"{_cardinal(hour)} cero {_cardinal(minute)}"
        return f"{_cardinal(hour)} {_cardinal(minute)}"
    if kind == "fraction":
        above, below = (int(x) for x in raw.split("/"))
        if below == 0:
            return raw
        if (above, below) == (1, 2):
            return "un medio"
        if (above, below) == (1, 3):
            return "un tercio"
        if below == 4:
            if above == 1:
                return "un cuarto"
            return f"{_cardinal(above)} cuartos"
        return f"{_cardinal(above)} sobre {_cardinal(below)}"
    if kind == "ordinal":
        value = int(raw[:-2] if raw.endswith("er") else raw[:-1])
        if raw.endswith("er"):
            return "primer" if value == 1 else _apocope(_ORDINAL_M[value])
        return _ORDINAL_F[value] if raw.endswith("ª") else _ORDINAL_M[value]
    if kind in {"usd", "clp", "eur_pre", "eur_post"}:
        numeral = re.search(r"\d+(?:[.,]\d+)*", raw).group()
        unit = {"usd": "dólares", "clp": "pesos", "eur_pre": "euros", "eur_post": "euros"}[kind]
        return f"{_spell_number(numeral, unit)} {unit}"
    if kind == "negative" and (match.start() == 0 or text[match.start() - 1].isspace()):
        return "menos " + _spell_number(raw[1:], _following_word(text, match.end()))
    if kind == "negative":
        return raw
    return _spell_number(raw, _following_word(text, match.end()))


def spell_numbers(text: str, lang: str = "es") -> str:
    """Expand Spanish prices, dates, clocks and numerals without changing IDs."""
    if lang != "es":
        return text
    return _TOKEN.sub(lambda match: _token_words(match, text), text)


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
