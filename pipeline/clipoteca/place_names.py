"""Shared proper-name heuristics, independent of optional studio packages."""

from __future__ import annotations

import re
import unicodedata


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", value) if unicodedata.category(char) != "Mn"
    )


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", _fold(text))


# Known geographic entities plus explicit multi-word title-cased names in queries.
# Match full token sequences, never a single component such as "Nui".
_PLACE_NAMES = (
    "rapa nui",
    "easter island",
    "isla de pascua",
    "valparaiso",
    "atacama",
    "torres del paine",
    "paine",
    "chiloe",
    "perito moreno",
    "patagonia",
    "santiago",
    "antofagasta",
    "punta arenas",
    "grey glacier",
    "glaciar grey",
    "glacier grey",
    "san rafael",
    "san rafael glacier",
    "glaciar san rafael",
    "laguna san rafael",
)
_NAME_SEQUENCE = re.compile(r"\b[A-Z\u00c0-\u00de][\w]+(?:\s+[A-Z\u00c0-\u00de][\w]+)+")


def query_names(queries):
    names = set()
    for query in queries:
        folded = " ".join(_words(query))
        for name in _PLACE_NAMES:
            if f" {name} " in f" {folded} ":
                names.add(name)
        # Internal capitalized proper names; sentence-initial single words
        # are ambiguous, so require a known entity or a multi-word sequence.
        if not query.isupper():
            for match in _NAME_SEQUENCE.finditer(query):
                names.add(" ".join(_words(match.group())))
    return {
        name
        for name in names
        if not any(name != other and f" {name} " in f" {other} " for other in names)
    }


# Conservative documented aliases for the same geographic entity.
_ALIAS_GROUPS = (
    ("rapa nui", "easter island", "isla de pascua"),
    ("torres del paine", "torres del paine national park"),
    ("chiloe", "chiloe island"),
    ("grey glacier", "glaciar grey", "glacier grey"),
    ("san rafael", "san rafael glacier", "glaciar san rafael", "laguna san rafael"),
)


def aliases(name: str) -> tuple[str, ...]:
    name = " ".join(_words(name))
    for group in _ALIAS_GROUPS:
        if name in group:
            return group
    return (name,)
