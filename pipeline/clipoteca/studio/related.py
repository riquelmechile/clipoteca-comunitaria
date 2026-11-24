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
    if not isinstance(explicit, dict):
        explicit = {}
    script = [
        {"term": term, "origin": "script"}
        for lang in ("es", "en")
        for term in (explicit[lang] if isinstance(explicit.get(lang), list) else [])
        if isinstance(term, str)
    ]
    for row in [
        *script,
        *(item for group in groups if isinstance(group, list) for item in group),
    ]:
        if len(result) >= MAX_RELATED:
            break
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("term"), str)
            or row.get("origin") not in ("script", "wikidata", "commons")
        ):
            continue
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


# Wikidata class identifiers, not a geographical name allowlist.
_COUNTRY_CLASSES = {"Q6256", "Q3624078", "Q15634554"}
_GEO_CLASSES = _COUNTRY_CLASSES | {
    "Q515",
    "Q3957",
    "Q532",
    "Q486972",
    "Q23442",
    "Q33837",
    "Q56061",
    "Q5107",
    "Q82794",
    "Q1187811",
    "Q15284",
    "Q22698",
    "Q159313",
    "Q46169",
    "Q473972",
    "Q207694",
    "Q16970",
    "Q839954",
    "Q570116",
}
_BAD_CLASSES = {
    "Q16521",
    "Q7366",
    "Q482994",
    "Q4830453",
    "Q43229",
    "Q11424",
    "Q5",
    "Q4167410",
    "Q13406463",
    "Q18608583",
    "Q134556",
    "Q17362920",
}
_BAD_DESCRIPTION = re.compile(
    r"\b(taxon|genus|species|insect|song|album|band|company|corporation|"
    r"electoral district|constituency|surname|given name|disambiguation|"
    r"taxon|genero|especie|cancion|album|empresa|circunscripcion)\b"
)
_GEO_QUALIFIERS = (
    "island",
    "islands",
    "province",
    "archipelago",
    "prefecture",
    "city",
    "municipality",
    "commune",
    "region",
    "district",
    "national park",
    "isla",
    "islas",
    "provincia",
    "archipielago",
    "prefectura",
    "ciudad",
    "comuna",
    "municipio",
    "parque nacional",
    "metropolitan area",
    "urban area",
)
_SCENE_QUALIFIERS = re.compile(
    r"\b(?:city|ciudad|temples?|templos?|shrines?|santuarios?|"
    r"tourism|turismo|footage|video|aerial|travel|viaje|landscape|paisaje|"
    r"landmarks?|monuments?|monumentos?|moai|beaches|playas|streets|calles)$"
)
_META_PREFIX = re.compile(
    r"^(?:books?|libros?|history|historia|culture|cultura|earthquakes?|"
    r"terremotos?|geography|geografia|geology|geologia|economy|economia|"
    r"education|educacion|aerial photographs?|photographs?|photography|"
    r"images?|imagenes|pictures?|featured|quality|categories|categorias|"
    r"maps?|mapas|people|personas|events?|eventos|flags?|banderas|"
    r"transport|transportation|politics|religion|demographics|"
    r"media|files|videos?|paintings?|art|heraldry|views?)\b"
)
_BUCKET_PREFIX = re.compile(
    r"^(?:populated places|settlements|cities|towns|villages|municipalities|"
    r"communes|localities|churches|chapels|temples|buddhist temples|"
    r"shinto shrines|shrines|buildings|monuments|archaeological sites|"
    r"tourist attractions|beaches|parks|national parks|volcanoes|ahu|"
    r"geography|localidades|comunas|iglesias|templos|playas|parques)\b"
)
_DATE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\b")


def _variants(seed):
    return {_key(value) for value in aliases(seed)}


def _seed_key(seed):
    # Aliases of one entity share their lookup and its render-local cache entry.
    return aliases(seed)[0]


def _named_terms(term):
    key = _key(term)
    return sorted(query_names([term]), key=lambda name: (key.find(name), -len(name), name))


def seeds(queries):
    """Prefer the specific subject, not its trailing country or scene qualifiers."""
    result = []
    for term in primary_queries(queries):
        named = _named_terms(term)
        if named:
            seed = named[0]
        else:
            seed = _key(term)
            # "Kyoto temples" and analogous queries should resolve Kyoto itself.
            while True:
                shortened = _SCENE_QUALIFIERS.sub("", seed).strip()
                if not shortened or shortened == seed:
                    break
                seed = shortened
        if 0 < len(seed.split()) <= 5:
            seed = _seed_key(seed)
            if seed not in result:
                result.append(seed)
    # Country/region-only alternatives that also qualify another seed are context.
    contexts = {name for term in primary_queries(queries) for name in _named_terms(term)[1:]}
    specific = [seed for seed in result if seed not in contexts]
    return tuple((specific or result)[:2])


def _context(queries, seed):
    """Resolve trailing named context as entities, not a hardcoded country list."""
    names = []
    variants = _variants(seed)
    for term in primary_queries(queries):
        for name in _named_terms(term):
            if name not in variants and name not in names:
                names.append(name)
        # Unknown places/countries can still be supplied as comma-separated context.
        parts = term.split(",")[1:]
        for part in parts:
            name = _key(part)
            if name and name not in variants and name not in names:
                names.append(name)
    return tuple(names[:2])


def _place_hint(queries, seed):
    for term in primary_queries(queries):
        if query_names([term]) or _SCENE_QUALIFIERS.search(_key(term)):
            return True
        if term[:1].isupper() and _key(term).startswith(seed):
            return True
    return False


async def _json(client, url, **params):
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def _optional_json(client, url, **params):
    try:
        return await _json(client, url, **params)
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _qid(value):
    return isinstance(value, str) and re.fullmatch(r"Q[1-9]\d*", value) is not None


def _claim_ids(entity, prop):
    result = set()
    for claim in entity.get("claims", {}).get(prop, []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and _qid(value.get("id")):
            result.add(value["id"])
    return result


def _label(entity):
    labels = entity.get("labels", {})
    return labels.get("es", {}).get("value") or labels.get("en", {}).get("value") or ""


def _geo(entity):
    return bool(entity.get("claims", {}).get("P625") or _claim_ids(entity, "P31") & _GEO_CLASSES)


def _bad(entity, hits):
    if _claim_ids(entity, "P31") & _BAD_CLASSES:
        return True
    descriptions = [hit.get("description", "") for hit in hits]
    return any(_BAD_DESCRIPTION.search(_key(description)) for description in descriptions)


def _root_match(label, variants):
    key = _key(label)
    if key in variants:
        return True
    return any(
        key in {f"{variant} {suffix}", f"{suffix} de {variant}", f"{suffix} of {variant}"}
        for variant in variants
        for suffix in _GEO_QUALIFIERS
    )


async def _search_entities(client, seed, lang):
    payload = await _optional_json(
        client,
        WIKIDATA,
        action="wbsearchentities",
        search=seed,
        language=lang,
        uselang=lang,
        type="item",
        limit=12,
        format="json",
    )
    return payload.get("search", [])


def _child_term(term, variants):
    # Commons disambiguation suffixes are not a search term ("Castro, Chile").
    term = term.split(",", 1)[0].strip()
    key = _key(term)
    if (
        not key
        or key in variants
        or len(term) > 80
        or _DATE.search(key)
        or _META_PREFIX.search(key)
        or re.search(r"\bby (?:media|type|year|country|location)\b|\bin art\b", key)
        or re.fullmatch(r"Q\d+", term)
    ):
        return None
    # A category describing its parent is not itself a landmark.
    for variant in variants:
        if re.search(rf"\b(?:of|in|about|from|on|de|en|sobre|del) {re.escape(variant)}$", key):
            return None
    return term


async def _wikidata(client, seed, context, state, *, place_hint):
    variants = _variants(seed)
    searches = [(seed, "en"), (seed, "es")]
    searches.extend((name, "en") for name in sorted(variants - {seed})[:2])
    search_terms = [*searches, *((name, "en") for name in context)]
    groups = await asyncio.gather(
        *(_search_entities(client, name, lang) for name, lang in search_terms)
    )
    matches = {}
    for hits in groups[: len(searches)]:
        for hit in hits:
            qid = hit.get("id")
            labels = [hit.get("label", ""), hit.get("match", {}).get("text", "")]
            if _qid(qid) and any(_root_match(label, variants) for label in labels):
                matches.setdefault(qid, []).append(hit)
    context_matches = {}
    for name, hits in zip(context, groups[len(searches) :], strict=True):
        for hit in hits:
            qid = hit.get("id")
            labels = [hit.get("label", ""), hit.get("match", {}).get("text", "")]
            if _qid(qid) and any(_key(label) == name for label in labels):
                context_matches.setdefault(qid, []).append(hit)
    ids = list(dict.fromkeys([*matches, *context_matches]))[:36]
    if not ids:
        return []
    data = await _optional_json(
        client,
        WIKIDATA,
        action="wbgetentities",
        ids="|".join(ids),
        props="claims|labels|aliases|sitelinks",
        languages="es|en",
        format="json",
    )
    entities = data.get("entities", {})
    countries = {
        qid
        for qid in context_matches
        if _claim_ids(entities.get(qid, {}), "P31") & _COUNTRY_CLASSES
    }
    eligible = [
        (qid, entities[qid])
        for qid, hits in matches.items()
        if qid in entities and not _bad(entities[qid], hits)
    ]
    geographic = [(qid, entity) for qid, entity in eligible if _geo(entity)]
    if countries:
        geographic = [
            (qid, entity)
            for qid, entity in geographic
            if _claim_ids(entity, "P17") & countries or qid in countries
        ]
    elif context:
        # A failed country lookup must not select a more popular foreign homonym.
        geographic = [
            (qid, entity)
            for qid, entity in geographic
            if any(
                f" {name} " in f" {_key(hit.get('description', ''))} "
                for name in context
                for hit in matches[qid]
            )
        ]
    geographic.sort(
        key=lambda row: (
            bool(_claim_ids(row[1], "P31") & _COUNTRY_CLASSES),
            -len(row[1].get("sitelinks", {})),
            row[0],
        )
    )
    if geographic:
        roots = geographic[:3]
        state["parent"] = _label(roots[0][1]) or seed
        state["geographic"] = True
        state["country_only"] = all(
            _claim_ids(entity, "P31") & _COUNTRY_CLASSES for _, entity in roots
        )
    elif (
        not place_hint
        and not countries
        and len(eligible) == 1
        and _key(_label(eligible[0][1])) in variants
    ):
        roots = eligible
    else:
        return []
    state["roots"] = [{"qid": qid, "label": _label(entity)} for qid, entity in roots]
    if not geographic:
        entity = roots[0][1]
        return [
            {"term": term, "origin": "wikidata"}
            for lang in ("es", "en")
            for alias in entity.get("aliases", {}).get(lang, [])
            if (term := _child_term(alias.get("value", ""), variants))
        ]
    values = " ".join(f"wd:{qid}" for qid, _ in roots)
    # Direct and two-level administrative relationships plus physical location.
    # Coordinates (or a known geographic class) exclude most unrelated entities.
    classes = " ".join(f"wd:{qid}" for qid in sorted(_GEO_CLASSES - _COUNTRY_CLASSES))
    query = f"""
SELECT ?item ?itemLabel ?links ?population WHERE {{
  {{
    SELECT DISTINCT ?item ?links ?population WHERE {{
      VALUES ?root {{ {values} }}
      {{ ?item wdt:P131 ?root . }}
      UNION {{ ?item wdt:P131/wdt:P131 ?root . }}
      UNION {{ ?item wdt:P276 ?root . }}
      UNION {{ ?item wdt:P706 ?root . }}
      UNION {{ ?root wdt:P527 ?item . }}
      UNION {{ ?item wdt:P361 ?root . }}
      FILTER(?item NOT IN ({", ".join(f"wd:{qid}" for qid, _ in roots)}))
      {{ ?item wdt:P625 ?coord . }}
      UNION {{ VALUES ?class {{ {classes} }} ?item wdt:P31 ?class . }}
      ?item wikibase:sitelinks ?links .
      FILTER(?links > 1)
      OPTIONAL {{ ?item wdt:P1082 ?population }}
    }}
    ORDER BY DESC(?links) DESC(COALESCE(?population, 0)) ?item
    LIMIT 40
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "es,en". }}
}}
ORDER BY DESC(?links) DESC(COALESCE(?population, 0)) ?item
"""
    payload = await _optional_json(client, WDQS, query=query, format="json")
    return [
        {"term": term, "origin": "wikidata"}
        for binding in payload.get("results", {}).get("bindings", [])
        if (term := _child_term(binding.get("itemLabel", {}).get("value", ""), variants))
    ]


async def _category_members(client, title):
    payload = await _optional_json(
        client,
        COMMONS,
        action="query",
        list="categorymembers",
        cmtitle=title,
        cmtype="subcat",
        cmlimit=100,
        format="json",
    )
    return [
        child["title"]
        for child in payload.get("query", {}).get("categorymembers", [])
        if child.get("ns") == 14 and child.get("title", "").startswith("Category:")
    ]


def _commons_root(title, variants, context):
    key = _key(title)
    if context:
        # Never use a bare homonymous root if a country was provided.
        return any(
            key in {f"{variant} {name}", f"{variant} de {name}", f"{variant} in {name}"}
            for variant in variants
            for name in context
        )
    return _root_match(title, variants)


async def _commons(client, seed, context, state):
    variants = _variants(seed)
    phrases = sorted(variants)
    if context:
        phrases = [
            f"{variant}{separator}{name}"
            for variant in phrases
            for name in context
            for separator in (" ", " de ", ", ")
        ]
    payload = await _optional_json(
        client,
        COMMONS,
        action="query",
        list="search",
        srnamespace=14,
        srsearch=" OR ".join(f'"{value}"' for value in phrases),
        srlimit=30,
        format="json",
    )
    roots = [
        hit["title"]
        for hit in payload.get("query", {}).get("search", [])
        if hit.get("title", "").startswith("Category:")
        and _commons_root(hit["title"][9:], variants, context)
    ][:3]
    if not roots:
        return []
    state["commons_roots"] = roots
    state["parent"] = roots[0][9:]
    state["geographic"] = True
    root_variants = variants | {_key(title[9:]) for title in roots}
    children = await asyncio.gather(*(_category_members(client, root) for root in roots))
    categories = list(dict.fromkeys(title for group in children for title in group))
    buckets = [
        title
        for title in categories
        if _BUCKET_PREFIX.search(_key(title[9:]))
        and not _DATE.search(_key(title[9:]))
        and not _child_term(title[9:], root_variants)
    ]
    # Prefer specific containers to generic geography, and cap network fanout.
    buckets.sort(key=lambda title: (_key(title[9:]).startswith("geography"), title))
    direct = [
        {"term": term, "origin": "commons"}
        for title in categories
        if (term := _child_term(title[9:], root_variants))
    ]
    state["terms"] = direct
    nested = await asyncio.gather(*(_category_members(client, title) for title in buckets[:8]))
    groups = [
        [
            {"term": term, "origin": "commons"}
            for title in group
            if (term := _child_term(title[9:], root_variants))
        ]
        for group in nested
    ]
    # Interleave buckets to avoid filling all slots with one kind of landmark.
    groups.append(direct)
    return [
        group[index]
        for index in range(MAX_RELATED)
        for group in groups
        if index < len(group)
    ]


async def _run_provider(function, client, seed, context, state, **kwargs):
    try:
        async with asyncio.timeout(LOOKUP_SECONDS - 0.25):
            state["terms"] = await function(client, seed, context, state, **kwargs)
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        pass


async def resolve_jobs(jobs, *, client=None, timeout=LOOKUP_SECONDS):
    """One six-second, render-local lookup budget; preserve completed provider results."""
    timeout = min(LOOKUP_SECONDS, timeout)
    owned = client is None
    if owned:
        client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=timeout)
    cache = {}
    tasks = []
    semaphore = asyncio.Semaphore(8)

    async def run(function, seed, context, state, **kwargs):
        async with semaphore:
            await _run_provider(function, client, seed, context, state, **kwargs)

    try:
        keys_by_job = []
        for job in jobs:
            queries = job["queries"]
            keys = []
            if len(merge_terms(queries)) < MAX_RELATED:
                for seed in seeds(queries):
                    context = _context(queries, seed)
                    place = _place_hint(queries, seed)
                    key = (seed, context, place)
                    keys.append(key)
                    if key in cache:
                        continue
                    wiki, commons = {}, {}
                    cache[key] = (wiki, commons)
                    tasks.append(
                        asyncio.create_task(
                            run(_wikidata, seed, context, wiki, place_hint=place)
                        )
                    )
                    if place:
                        tasks.append(asyncio.create_task(run(_commons, seed, context, commons)))
            keys_by_job.append(keys)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        diagnostics = []
        for job, keys in zip(jobs, keys_by_job, strict=True):
            states = [(key, *cache[key]) for key in keys]
            if any(
                wiki.get("geographic") and not wiki.get("country_only") for _, wiki, _ in states
            ):
                states = [row for row in states if not row[1].get("country_only")]
            groups = []
            for _, wiki, commons in states:
                groups.extend((wiki.get("terms", []), commons.get("terms", [])))
            job["related_terms"] = merge_terms(job["queries"], *groups)
            for (seed, _, _), wiki, commons in states:
                if wiki.get("geographic") or commons.get("geographic"):
                    job["related_parent"] = _parent_label(
                        job["queries"], seed, _context(job["queries"], seed)
                    )
                    break
            diagnostics.append(
                {
                    "seeds": [key[0] for key in keys],
                    "roots": [root for _, wiki, _ in states for root in wiki.get("roots", [])],
                    "commons_roots": [
                        root
                        for _, _, commons in states
                        for root in commons.get("commons_roots", [])
                    ],
                }
            )
        return diagnostics
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if owned:
            await client.aclose()


def related_terms(job):
    return merge_terms(job["queries"], job.get("related_terms", []))


def _parent_label(queries, seed, context=()):
    variants = _variants(seed)
    parent = next(
        (query for query in primary_queries(queries) if _key(query) in variants),
        seed,
    )
    if context and not any(f" {name} " in f" {_key(parent)} " for name in context):
        parent = " ".join((parent, *context))
    return parent


def related_parent(job):
    parent = job.get("related_parent")
    if parent:
        return parent
    queries = job["queries"]
    names = seeds(queries)
    if names and any(query_names([query]) for query in primary_queries(queries)):
        return _parent_label(queries, names[0], _context(queries, names[0]))
    return None


def related_search_query(job, term):
    """Qualify an ambiguous related name with its parent; never alter clip metadata."""
    parent = related_parent(job)
    if not parent:
        return term
    if any(f" {variant} " in f" {_key(term)} " for variant in _variants(parent)):
        return term
    return f"{term} {parent}"
