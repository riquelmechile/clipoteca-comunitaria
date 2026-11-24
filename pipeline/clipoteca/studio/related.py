"""Bounded related terms from script, Wikidata and Commons, never searchable clip tags."""

from __future__ import annotations

import asyncio
import re

import httpx

from ..place_names import _words, aliases, query_names

MAX_RELATED = 8
LOOKUP_SECONDS = 6
WIKIDATA = "https://www.wikidata.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"
COMMONS = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Clipoteca/3.0 (https://github.com/riquelmechile/clipoteca-comunitaria)"


def _key(term):
    return " ".join(_words(term))


def primary_queries(queries):
    return [term for lang in ("en", "es") for term in queries.get(lang, [])]


def merge_terms(queries, *groups):
    """Script terms win; deduplicate against primary terms and each other."""
    seen = {_key(term) for term in primary_queries(queries)}
    result = []
    explicit = queries.get("related", {})
    script = [
        {"term": term, "origin": "script"}
        for lang in ("es", "en")
        for term in explicit.get(lang, [])
    ]
    for row in [*script, *(item for group in groups for item in group)]:
        if len(result) >= MAX_RELATED:
            break
        term = row["term"].strip()
        key = _key(term)
        if (
            key
            and key not in seen
            and len(term) <= 80
            and not any(ord(char) < 32 or ord(char) == 127 for char in term)
        ):
            seen.add(key)
            result.append({"term": term, "origin": row["origin"]})
    return result


def seeds(queries):
    terms = primary_queries(queries)
    names = sorted(query_names(terms), key=lambda name: (-len(name.split()), name))
    if names:
        return tuple(names[:2])
    # A non-geographic topic is eligible only for exact entity lookup, never fuzzy expansion.
    return tuple(dict.fromkeys(_key(term) for term in terms if 0 < len(_words(term)) <= 4))[:2]


async def _json(client, url, **params):
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _variants(seed):
    return {_key(value) for value in aliases(seed)}


async def _wikidata(client, seed):
    variants = _variants(seed)
    roots = {}
    for lang in ("en", "es"):
        payload = await _json(
            client,
            WIKIDATA,
            action="wbsearchentities",
            search=seed,
            language=lang,
            uselang=lang,
            type="item",
            limit=8,
            format="json",
        )
        for hit in payload.get("search", []):
            qid = hit.get("id", "")
            labels = [hit.get("label", ""), hit.get("match", {}).get("text", "")]
            if re.fullmatch(r"Q[1-9]\d*", qid) and any(_key(x) in variants for x in labels):
                roots[qid] = hit
    if len(roots) != 1:
        # Ambiguous labels are not reliable geographic or topical evidence.
        return []
    qid, hit = next(iter(roots.items()))
    # Only verified entity IDs are interpolated into the query.
    query = f"""
SELECT ?item ?itemLabel ?population ?links WHERE {{
  VALUES ?root {{ wd:{qid} }}
  {{
    ?item wdt:P131 ?root .
  }} UNION {{
    ?item wdt:P706 ?root .
  }} UNION {{
    ?root wdt:P527 ?item .
  }} UNION {{
    ?item wdt:P361 ?root .
  }} UNION {{
    ?item wdt:P131/wdt:P131 ?root .
  }}
  FILTER(?item != ?root)
  OPTIONAL {{ ?item wdt:P1082 ?population }}
  OPTIONAL {{ ?item wikibase:sitelinks ?links }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
ORDER BY DESC(COALESCE(?population,0)) DESC(COALESCE(?links,0)) ?item
LIMIT 30
"""
    rows = []
    try:
        payload = await _json(client, WDQS, query=query, format="json")
        for binding in payload.get("results", {}).get("bindings", []):
            term = binding.get("itemLabel", {}).get("value", "")
            if term and not re.fullmatch(r"Q\d+", term):
                rows.append({"term": term, "origin": "wikidata"})
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass
    # Alias and label evidence comes from the exact entity match, not search suggestions.
    for term in [hit.get("label", ""), *hit.get("aliases", [])]:
        if isinstance(term, str) and term:
            rows.append({"term": term, "origin": "wikidata"})
    return rows


async def _commons(client, seed):
    variants = _variants(seed)
    payload = await _json(
        client,
        COMMONS,
        action="query",
        list="search",
        srnamespace=14,
        srsearch=" OR ".join(f'"{value}"' for value in sorted(variants)),
        srlimit=12,
        format="json",
    )
    roots = []
    for hit in payload.get("query", {}).get("search", []):
        title = hit.get("title", "")
        if title.startswith("Category:") and _key(title[9:]) in variants:
            roots.append(title)
    rows = []
    for title in sorted(set(roots))[:2]:
        payload = await _json(
            client,
            COMMONS,
            action="query",
            list="categorymembers",
            cmtitle=title,
            cmtype="subcat",
            cmlimit=50,
            format="json",
        )
        for child in payload.get("query", {}).get("categorymembers", []):
            label = child.get("title", "")
            if child.get("ns") != 14 or not label.startswith("Category:"):
                continue
            term = label[9:]
            key = _key(term)
            if key.startswith(("maps of ", "people from ", "photographs of ", "images of ")):
                continue
            if key not in variants:
                rows.append({"term": term, "origin": "commons"})
    return rows


async def _safe_lookup(function, client, seed):
    try:
        async with asyncio.timeout(LOOKUP_SECONDS):
            return await function(client, seed)
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return []


async def _lookup(client, seed, *, place):
    calls = [_safe_lookup(_wikidata, client, seed)]
    if place:
        calls.append(_safe_lookup(_commons, client, seed))
    groups = await asyncio.gather(*calls)
    return [row for group in groups for row in group]


async def resolve_jobs(jobs, *, client=None, timeout=LOOKUP_SECONDS):
    """One bounded lookup per distinct seed per render; offline explicit terms survive failure."""
    owned = client is None
    if owned:
        client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout)
    cache = {}
    semaphore = asyncio.Semaphore(4)

    async def run(seed, place):
        async with semaphore:
            return await _lookup(client, seed, place=place)

    try:
        keys_by_job = []
        for job in jobs:
            queries = job["queries"]
            keys = []
            if len(merge_terms(queries)) < MAX_RELATED:
                place = bool(query_names(primary_queries(queries)))
                for seed in seeds(queries):
                    key = (seed, place)
                    keys.append(key)
                    if key not in cache:
                        cache[key] = asyncio.create_task(run(seed, place))
            keys_by_job.append(keys)
        if cache:
            _, pending = await asyncio.wait(cache.values(), timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        for job, keys in zip(jobs, keys_by_job, strict=True):
            groups = [cache[key].result() for key in keys if not cache[key].cancelled()]
            job["related_terms"] = merge_terms(job["queries"], *groups)
    finally:
        for task in cache.values():
            if not task.done():
                task.cancel()
        if cache:
            await asyncio.gather(*cache.values(), return_exceptions=True)
        if owned:
            await client.aclose()


def related_terms(job):
    return merge_terms(job["queries"], job.get("related_terms", []))
