"""Validated, versioned persistent related-term cache (no credentials or clip tags)."""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = ROOT / "data" / "related_terms.json"
TTL = timedelta(days=30)
SCHEMA_PATH = ROOT / "schemas" / "related_terms.schema.json"


def cache_key(key):
    seed, context, place, topic = key
    return json.dumps([seed, list(context), place, topic], ensure_ascii=True, separators=(",", ":"))


def _safe(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema["$defs"]["entry"])


def valid(entry):
    try:
        if not _validator().is_valid(entry):
            return False
        strings = [entry["seed"], *entry["context"], *entry["commons_roots"]]
        strings += [row["term"] for row in entry["terms"]]
        strings += [row["label"] for row in entry["roots"]]
        timestamp = datetime.fromisoformat(entry["date"])
        from ..place_names import _words

        normalized = [entry["seed"], *entry["context"]]
        return (
            all(_safe(value) for value in strings)
            and all(value == " ".join(_words(value)) for value in normalized)
            and entry["context"] == sorted(set(entry["context"]))
            and timestamp.tzinfo is not None
            and timestamp <= datetime.now(UTC) + timedelta(minutes=5)
        )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def entry_key(entry):
    return cache_key((entry["seed"], tuple(entry["context"]), entry["place"], entry["topic"]))


def fresh(entry, now=None):
    age = (now or datetime.now(UTC)) - datetime.fromisoformat(entry["date"])
    return timedelta(0) <= age < TTL


def decode_cache(payload):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {}
    return merge_entries({}, entries)


def load_cache(path=CACHE_PATH):
    try:
        return decode_cache(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def merge_entries(current, entries):
    result = dict(current)
    for entry in entries:
        if not valid(entry):
            continue
        key = entry_key(entry)
        previous = result.get(key)
        if previous is None or datetime.fromisoformat(entry["date"]) > datetime.fromisoformat(
            previous["date"]
        ):
            result[key] = entry
    return result


def encode_cache(entries):
    return {"version": 1, "entries": [entries[key] for key in sorted(entries)]}


def save_cache(path, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(encode_cache(entries), handle, ensure_ascii=True, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def make_entry(key, wiki, commons):
    from .related import merge_terms, _rank_seed_terms

    seed, context, place, topic = key
    terms = merge_terms({"en": [seed], "es": []}, _rank_seed_terms(key, wiki, commons))
    if not terms:
        return None
    entry = {
        "seed": seed,
        "context": list(context),
        "place": place,
        "topic": topic,
        "date": datetime.now(UTC).isoformat(),
        "terms": terms,
        "roots": wiki.get("roots", [])[:3],
        "commons_roots": commons.get("commons_roots", [])[:3],
        "wiki_geo": bool(wiki.get("geographic")),
        "commons_geo": bool(commons.get("geographic")),
        "country_only": bool(wiki.get("country_only")),
    }
    return entry if valid(entry) else None


def restore(entry):
    return (
        {
            "terms": [row for row in entry["terms"] if row["origin"] == "wikidata"],
            "roots": entry["roots"],
            "geographic": entry["wiki_geo"],
            "country_only": entry["country_only"],
        },
        {
            "terms": [row for row in entry["terms"] if row["origin"] == "commons"],
            "commons_roots": entry["commons_roots"],
            "geographic": entry["commons_geo"],
        },
    )
