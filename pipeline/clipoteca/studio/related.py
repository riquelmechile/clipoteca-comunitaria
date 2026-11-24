"""Bounded related terms from script, Wikidata and Commons, never searchable clip tags."""

from __future__ import annotations

import asyncio
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from ..place_names import _words, aliases, query_names
from .related_cache import cache_key, fresh, make_entry, restore

MAX_RELATED = 8
LOOKUP_SECONDS = 8
SEED_CONCURRENCY = 4
BATCH_SECONDS = 25
HOST_CONCURRENCY = 3
_REQUEST_SECONDS = 4.5
_MAX_RETRY_AFTER = 3.0
_HOSTS = ContextVar("related_hosts", default=None)
_RESPONSE_CACHE = ContextVar("related_response_cache", default=None)
_REQUEST_LOCKS = ContextVar("related_request_locks", default=None)
_DEADLINE = ContextVar("related_deadline", default=None)
_ERRORS = ContextVar("related_errors", default=None)
WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = (
    "Clipoteca/3.0 related-place discovery (https://github.com/riquelmechile/clipoteca-comunitaria)"
)


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
    "Q8514",
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
    "desert",
    "desierto",
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
    r"media|files|videos?|paintings?|art|heraldry|views?|things|ships|"
    r"cultural properties|islets adjacent|buildings|churches|temples)\b"
)
_BUCKET_PREFIX = re.compile(
    r"^(?:populated places|settlements|cities|towns|villages|municipalities|"
    r"communes|localities|churches|chapels|temples|buddhist temples|"
    r"shinto shrines|shrines|buildings|monuments|archaeological sites|"
    r"tourist attractions|beaches|parks|national parks|volcanoes|ahu|"
    r"geography|localidades|comunas|iglesias|templos|playas|parques)\b"
)
_DATE = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\b")
_META_PHRASE = re.compile(
    r"\b(?:named after|adjacent to|designated by|metropolitan area|urban area|"
    r"by (?:commune|media|type|year|country|location|district)|in art)\b"
)
_PLURAL_CATEGORY = re.compile(
    r"^\w+(?: \w+){0,4}s (?:in|of|on|by|about|from|named after|adjacent to|designated by)\b"
)
_TOPIC_PATTERNS = {
    "temple": re.compile(r"\b(?:temples?|templos?|shrines?|santuarios?)\b"),
    "desert": re.compile(r"\b(?:deserts?|desiertos?)\b"),
}
_ROOT_TOPICS = {"desert"}


def _topic(queries, seed):
    for term in primary_queries(queries):
        key = _key(term)
        for variant in _variants(seed):
            key = re.sub(rf"\b{re.escape(variant)}\b", "", key)
        for topic, pattern in _TOPIC_PATTERNS.items():
            if pattern.search(key):
                return topic
    return None


def _topic_matches(topic, label):
    return not topic or bool(_TOPIC_PATTERNS[topic].search(_key(label)))


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
            seed = _key(term.split(",", 1)[0])
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


def _retry_delay(value):
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.5


def _record_error(params, exc, *, status=None, retry=False, delay=None):
    errors = _ERRORS.get()
    if errors is not None:
        row = {
            "action": params.get("list") or params.get("action"),
            "query": params.get("srsearch") or params.get("search") or params.get("cmtitle"),
            "error": type(exc).__name__,
            "detail": str(exc)[:160],
            "http_status": status,
            "retry": retry,
        }
        if delay is not None:
            row["retry_after_s"] = round(delay, 3)
        errors.append(row)


async def _json(client, url, **params):
    locks = _REQUEST_LOCKS.get()
    if locks is None:
        return await _request_json(client, url, **params)
    key = (url, tuple(sorted(params.items())))
    lock = locks.setdefault(key, asyncio.Lock())
    async with lock:
        return await _request_json(client, url, **params)


async def _request_json(client, url, **params):
    loop = asyncio.get_running_loop()
    hosts = _HOSTS.get()
    host = urlsplit(url).hostname
    gate = (
        hosts[host]
        if hosts is not None
        else {"lock": asyncio.Semaphore(HOST_CONCURRENCY), "until": 0.0}
    )
    cache = _RESPONSE_CACHE.get()
    key = (url, tuple(sorted(params.items())))
    if cache is not None and key in cache:
        return cache[key]
    deadline = _DEADLINE.get()
    for attempt in range(2):
        # Cooldown is shared across all seeds using the same Wikimedia host.
        async with gate["lock"]:
            if gate["until"] > loop.time():
                delay = gate["until"] - loop.time()
                if deadline is not None and loop.time() + delay >= deadline:
                    raise TimeoutError("host Retry-After exceeds remaining lookup budget")
                await asyncio.sleep(delay)
                if gate["until"] > loop.time():
                    raise TimeoutError("host cooldown extended during lookup")
            remaining = deadline - loop.time() if deadline is not None else _REQUEST_SECONDS
            async with asyncio.timeout(min(_REQUEST_SECONDS, remaining)):
                response = await client.get(url, params=params)
            payload = {}
            if response.is_success:
                payload = response.json()
            api_error = payload.get("error", {})
            transient = response.status_code in (429, 503)
            if transient:
                delay = min(_MAX_RETRY_AFTER, _retry_delay(response.headers.get("Retry-After")))
                gate["until"] = max(gate["until"], loop.time() + delay)
                if attempt == 0:
                    _record_error(
                        params,
                        ValueError("Wikimedia throttled request"),
                        status=response.status_code,
                        retry=True,
                        delay=delay,
                    )
                    continue
            response.raise_for_status()
            if api_error:
                raise ValueError(str(api_error.get("code", "api_error")))
            if cache is not None:
                cache[key] = payload
            return payload
    raise RuntimeError("unreachable retry state")


async def _optional_json(client, url, **params):
    try:
        return await _json(client, url, **params)
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
        _record_error(params, exc, status=response.status_code if response is not None else None)
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
    if any(key in {f"national park {variant}", f"parque nacional {variant}"} for variant in variants):
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


_UNFILMABLE = re.compile(
    r"\b(?:experiment|experiments|scientific instrument|telescope array|"
    r"radio telescope|cosmology project|observational program|"
    r"experimento|instrumento cientifico|programa|program|programme|"
    r"derrumbe|collapse|accident|disaster|accidente|hike|trekking|"
    r"walking tour|excursion|animita|animitas|memorial to|memorial de|"
    r"archive|archives|archivo|archivos|administrative office|"
    r"government agency|government institution|institucion administrativa)\b"
)
_SETTLEMENT_BUCKET = re.compile(
    r"^(?:populated places|settlements|cities|towns|villages|municipalities|"
    r"communes|localities|localidades|comunas|municipios)\b"
)


def _unfilmable(value):
    key = _key(value)
    return bool(
        _UNFILMABLE.search(key)
        or re.search(r"\b(?:array|collaboration|survey project)\b", key)
        or (len(value) >= 7 and value.isalpha() and value.isupper())
    )


def _place_priority(entity):
    if _claim_ids(entity, "P31") & {"Q515", "Q3957", "Q532", "Q486972", "Q15284"}:
        return 0
    if re.search(
        r"\b(?:municipality|commune|town|village|city|neighborhood|"
        r"municipio|comuna|localidad|ciudad|barrio)\b",
        _key(_entity_text(entity, [])),
    ):
        return 0
    return 1


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
        or _META_PHRASE.search(key)
        or _PLURAL_CATEGORY.search(key)
        or _unfilmable(term)
        or re.search(r"\b(?:region|province|provincia|prefecture|prefectura)$", key)
        or re.fullmatch(r"Q\d+", term)
    ):
        return None
    # A category describing its parent is not itself a landmark.
    for variant in variants:
        if re.search(rf"\b(?:of|in|about|from|on|de|en|sobre|del) {re.escape(variant)}$", key):
            return None
    return term


async def _wikidata(client, seed, context, state, *, place_hint, topic, roots_ready):
    variants = _variants(seed)
    # One entity search per name; bilingual labels are fetched together below.
    searches = [(seed, "en")]
    if topic in _ROOT_TOPICS:
        searches = [(f"{seed} {topic}", "en"), (seed, "en")]
    elif seed == "torres del paine":
        searches = [(f"{seed} national park", "en"), (seed, "en")]
    search_terms = [*searches, *((name, "en") for name in context)]
    groups = await asyncio.gather(
        *(_search_entities(client, name, lang) for name, lang in search_terms)
    )
    if not any(groups[: len(searches)]) and len(variants) > 1:
        alternate = sorted(variants - {seed})[0]
        groups.insert(len(searches), await _search_entities(client, alternate, "en"))
        searches.append((alternate, "en"))
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
        props="claims|labels|aliases|sitelinks|descriptions",
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
    if any("torres del paine" in variant for variant in variants):
        geographic = [(qid, entity) for qid, entity in geographic
                      if "parque nacional" in _key(_label(entity)) or "national park" in _key(_label(entity))
                      or "Q46169" in _claim_ids(entity, "P31")]
    if topic in _ROOT_TOPICS:
        geographic = [
            (qid, entity)
            for qid, entity in geographic
            if _root_topic_matches(topic, entity, matches[qid])
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
        and not topic
        and not countries
        and len(eligible) == 1
        and _key(_label(eligible[0][1])) in variants
    ):
        roots = eligible
    else:
        return []
    state["roots"] = [{"qid": qid, "label": _label(entity)} for qid, entity in roots]
    state["commons_titles"] = list(
        dict.fromkeys(title for _, entity in roots for title in _entity_categories(entity))
    )
    roots_ready.set()
    if not geographic:
        entity = roots[0][1]
        return [
            {"term": term, "origin": "wikidata"}
            for lang in ("es", "en")
            for alias in entity.get("aliases", {}).get(lang, [])
            if (term := _child_term(alias.get("value", ""), variants))
        ]
    # CirrusSearch uses the indexed statements and incoming links; avoid WDQS.
    child_topic = topic if topic not in _ROOT_TOPICS else None
    root_ids = {qid for qid, _ in roots}
    # Combine statements into one ranked request per root, instead of 12-24 calls.
    specifications = [
        (qid, ("P131", "P276", "P706", "P361"), child_topic or "") for qid, _ in roots
    ]
    groups = await asyncio.gather(
        *(_search_children(client, qid, props, keyword) for qid, props, keyword in specifications)
    )
    # Stable round robin across root/relationship rankings, not completion order.
    ids = list(dict.fromkeys(qid for qid in _interleave(groups, limit=20) if qid not in root_ids))
    parts = [
        qid
        for _, entity in roots
        for qid in sorted(_claim_ids(entity, "P527"))
        if qid not in root_ids
    ]
    ids = list(dict.fromkeys([*ids, *parts]))[:40]
    if not ids:
        return []
    # Preserve verified children after each completed label batch, even if another
    # batch runs out of the seed budget. Final ordering is independent of latency.
    children = {}

    async def load(batch):
        children.update(await _child_entities(client, batch))
        rows = []
        for qid in ids:
            entity = children.get(qid, {})
            if not _geo(entity) or _bad(entity, []) or _unfilmable(_entity_text(entity, [])):
                continue
            if child_topic and not _topic_matches(child_topic, _entity_text(entity, [])):
                continue
            if (
                countries
                and _claim_ids(entity, "P17")
                and not _claim_ids(entity, "P17") & countries
            ):
                continue
            term = _child_term(_label(entity), variants)
            if term:
                rows.append(
                    (
                        _place_priority(entity),
                        {"term": term, "origin": "wikidata"},
                    )
                )
        state["terms"] = [row for _, row in sorted(rows, key=lambda pair: pair[0])]

    await asyncio.gather(*(load(ids[i : i + 40]) for i in range(0, len(ids), 40)))
    return state.get("terms", [])


def _root_topic_matches(topic, entity, hits):
    # A region described as being in a desert is not itself the desert.
    if topic == "desert":
        if "Q8514" in _claim_ids(entity, "P31"):
            return True
        for label in entity.get("labels", {}).values():
            if _topic_matches(topic, label.get("value", "")):
                return True
        descriptions = [
            entry.get("value", "") for entry in entity.get("descriptions", {}).values()
        ] + [hit.get("description", "") for hit in hits]
        return any(
            re.match(r"^(?:a |the |un |el )?(?:desert|desierto)\b", _key(value))
            for value in descriptions
        )
    return _topic_matches(topic, _entity_text(entity, hits))


def _entity_text(entity, hits):
    values = [
        data.get("value", "")
        for field in ("labels", "descriptions")
        for data in entity.get(field, {}).values()
    ]
    return " ".join([*values, *(hit.get("description", "") for hit in hits)])


def _entity_categories(entity):
    titles = []
    link = entity.get("sitelinks", {}).get("commonswiki", {}).get("title", "")
    if link.startswith("Category:"):
        titles.append(link)
    for claim in entity.get("claims", {}).get("P373", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(value, str) and value.strip():
            titles.append(f"Category:{value}")
    return titles


def _interleave(groups, *, limit=MAX_RELATED):
    return [group[index] for index in range(limit) for group in groups if index < len(group)]


async def _search_children(client, root, props, keyword):
    query = "(" + " OR ".join(f"haswbstatement:{prop}={root}" for prop in props) + ")"
    if keyword:
        query += " (temple OR shrine)" if keyword == "temple" else f" {keyword}"
    payload = await _optional_json(
        client,
        WIKIDATA,
        action="query",
        list="search",
        srnamespace=0,
        srsearch=query,
        srsort="incoming_links_desc",
        srlimit=16,
        format="json",
    )
    return [
        hit["title"] for hit in payload.get("query", {}).get("search", []) if _qid(hit.get("title"))
    ]


async def _child_entities(client, ids):
    payload = await _optional_json(
        client,
        WIKIDATA,
        action="wbgetentities",
        ids="|".join(ids),
        props="labels|descriptions|claims",
        languages="es|en",
        format="json",
    )
    return payload.get("entities", {})


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
            or (key == f"{variant} desert {name}" or key == f"desierto de {variant} {name}")
            for variant in variants
            for name in context
        )
    return _root_match(title, variants)


async def _commons(client, seed, context, state, *, topic, wiki, roots_ready):
    variants = _variants(seed)
    phrases = sorted(variants)
    if topic == "temple":
        phrases += [f"{variant} temples" for variant in sorted(variants)]
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
        and (_commons_root(hit["title"][9:], variants, context)
             or (topic == "temple" and any(
                 _key(hit["title"][9:]) in {f"temples in {variant}", f"temples of {variant}",
                                           f"buddhist temples in {variant}", f"shrines in {variant}"}
                 for variant in variants) and not context))
    ][:3]
    if context or topic in _ROOT_TOPICS:
        # The verified root carries its own Commons category even if text search misses it.
        # Never wait for Wikidata: use verified roots only if already available.
        verified = wiki.get("commons_titles", []) if roots_ready.is_set() else []
        if topic in _ROOT_TOPICS:
            roots = (
                list(dict.fromkeys([*verified, *[root for root in roots if _topic_matches(topic, root)]]))[:3]
                if verified and wiki.get("geographic")
                else [root for root in roots if _topic_matches(topic, root)]
            )
        else:
            roots = list(dict.fromkeys([*verified, *roots]))[:3]
    if not roots and roots_ready.is_set() and (not topic or wiki.get("geographic")):
        # A late verified root may still be used without waiting for it.
        roots = wiki.get("commons_titles", [])[:3]
    if any("torres del paine" in variant for variant in variants):
        roots = [root for root in roots if "national park" in _key(root) or "parque nacional" in _key(root)]
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
        # "X by commune/type/year" only holds further containers, never terms.
        and not _META_PHRASE.search(_key(title[9:]))
        and not _child_term(title[9:], root_variants)
    ]
    # Prefer specific containers to generic geography, and cap network fanout.
    child_topic = topic if topic not in _ROOT_TOPICS else None
    if child_topic:
        buckets = [title for title in buckets if _topic_matches(child_topic, title)]
    buckets.sort(
        key=lambda title: (
            not _SETTLEMENT_BUCKET.search(_key(title[9:])),
            _key(title[9:]).startswith("geography"),
            title,
        )
    )
    topical_root = child_topic == "temple" and all(_topic_matches(child_topic, root) for root in roots)
    direct = [
        {"term": term, "origin": "commons"}
        for title in categories
        if (term := _child_term(title[9:], root_variants))
        and (topical_root or _topic_matches(child_topic, term))
    ]
    state["terms"] = direct
    # Retain completed buckets at the deadline, in fixed bucket order.
    buckets = buckets[:4]
    groups = [[] for _ in buckets]

    async def descend(index, title):
        members = await _category_members(client, title)
        groups[index] = [
            {"term": term, "origin": "commons"}
            for member in members
            if (term := _child_term(member[9:], root_variants))
            and (_topic_matches(child_topic, term) or _topic_matches(child_topic, title))
        ]
        # Settlement containers outrank individual landmarks.
        towns = [
            row
            for bucket, group in zip(buckets, groups, strict=True)
            if _SETTLEMENT_BUCKET.search(_key(bucket[9:]))
            for row in group
        ]
        other = [
            group
            for bucket, group in zip(buckets, groups, strict=True)
            if not _SETTLEMENT_BUCKET.search(_key(bucket[9:]))
        ]
        state["terms"] = towns + _interleave([*other, direct])

    await asyncio.gather(*(descend(i, title) for i, title in enumerate(buckets)))
    return state.get("terms", [])


async def _run_provider(function, client, seed, context, state, timeout, **kwargs):
    errors = state.setdefault("errors", [])
    token = _ERRORS.set(errors)
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline_token = _DEADLINE.set(started + timeout)
    state["attempts"] = state.get("attempts", 0) + 1
    try:
        async with asyncio.timeout(timeout):
            result = await function(client, seed, context, state, **kwargs)
            if result or not state.get("terms"):
                state["terms"] = result
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
        _record_error({}, exc)
    except asyncio.CancelledError:
        _record_error({}, TimeoutError("batch deadline"))
        raise
    finally:
        state["elapsed_s"] = round(state.get("elapsed_s", 0) + loop.time() - started, 3)
        if function is _wikidata:
            kwargs["roots_ready"].set()
        _DEADLINE.reset(deadline_token)
        _ERRORS.reset(token)


def _provider_terms(wiki, commons):
    wd = wiki.get("terms", [])
    cm = commons.get("terms", [])
    groups = []
    for i in range(MAX_RELATED):
        groups.extend(wd[2 * i : 2 * i + 2])
        groups.extend(cm[i : i + 1])
    return groups


async def resolve_jobs(jobs, *, client=None, timeout=LOOKUP_SECONDS, batch_timeout=BATCH_SECONDS,
                       persistent_cache=None):
    """Independent seed budgets, one bounded batch, a second chance for errored seeds."""
    timeout = min(LOOKUP_SECONDS, timeout)
    batch_timeout = min(BATCH_SECONDS, batch_timeout)
    owned = client is None
    if owned:
        client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            limits=httpx.Limits(max_connections=2 * HOST_CONCURRENCY),
        )
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + batch_timeout
    hosts = {
        urlsplit(url).hostname: {"lock": asyncio.Semaphore(HOST_CONCURRENCY), "until": 0.0}
        for url in (WIKIDATA, COMMONS)
    }
    host_token = _HOSTS.set(hosts)
    response_token = _RESPONSE_CACHE.set({})
    locks_token = _REQUEST_LOCKS.set({})
    cache = {}
    persistent = {} if persistent_cache is None else persistent_cache
    fresh_keys = set()
    stale = {}
    updates = {}
    tasks = []
    semaphore = asyncio.Semaphore(SEED_CONCURRENCY)

    async def run(key):
        seed, context, place, topic = key
        wiki, commons = cache[key]
        async with semaphore:
            remaining = deadline - loop.time()
            if remaining <= 0:
                wiki.setdefault("errors", []).append(
                    {
                        "error": "TimeoutError",
                        "detail": "batch deadline before admission",
                        "http_status": None,
                        "retry": False,
                    }
                )
                return
            budget = min(timeout, remaining)
            roots_ready = asyncio.Event()
            calls = [
                _run_provider(
                    _wikidata,
                    client,
                    seed,
                    context,
                    wiki,
                    budget,
                    place_hint=place,
                    topic=topic,
                    roots_ready=roots_ready,
                )
            ]
            if place:
                calls.append(
                    _run_provider(
                        _commons,
                        client,
                        seed,
                        context,
                        commons,
                        budget,
                        topic=topic,
                        wiki=wiki,
                        roots_ready=roots_ready,
                    )
                )
            await asyncio.gather(*calls)

    async def phase(keys):
        if not keys:
            return
        batch = [asyncio.create_task(run(key)) for key in keys]
        tasks.extend(batch)
        _, pending = await asyncio.wait(batch, timeout=max(0.0, deadline - loop.time()))
        for task in pending:
            task.cancel()
        await asyncio.gather(*batch, return_exceptions=True)
        for key, task in zip(keys, batch, strict=True):
            wiki, _ = cache[key]
            if task.cancelled() and not wiki.get("attempts"):
                wiki.setdefault("errors", []).append(
                    {
                        "error": "TimeoutError",
                        "detail": "batch deadline before admission",
                        "http_status": None,
                        "retry": False,
                    }
                )
            elif not task.cancelled() and task.exception() is not None:
                exc = task.exception()
                wiki.setdefault("errors", []).append(
                    {
                        "error": type(exc).__name__,
                        "detail": str(exc)[:160],
                        "http_status": None,
                        "retry": False,
                    }
                )

    def scene_states(keys):
        states = [(key, *cache[key]) for key in keys]
        if any(wiki.get("geographic") and not wiki.get("country_only") for _, wiki, _ in states):
            return [row for row in states if not row[1].get("country_only")]
        return states

    def terms(job, keys):
        return merge_terms(
            job["queries"],
            *[_provider_terms(wiki, commons) for _, wiki, commons in scene_states(keys)],
        )

    try:
        keys_by_job = []
        for job in jobs:
            queries = job["queries"]
            keys = []
            if len(merge_terms(queries)) < MAX_RELATED:
                for seed in seeds(queries):
                    context = tuple(sorted(_context(queries, seed)))
                    place = _place_hint(queries, seed)
                    topic = _topic(queries, seed)
                    key = (seed, context, place, topic)
                    keys.append(key)
                    if key not in cache:
                        entry = persistent.get(cache_key(key))
                        if entry and fresh(entry):
                            cache[key] = restore(entry)
                            fresh_keys.add(key)
                        else:
                            cache[key] = ({}, {})
                            if entry:
                                stale[key] = entry
            keys_by_job.append(keys)
        await phase([key for key in cache if key not in fresh_keys])
        # Retry only a scene that has no terms and actual errors; an empty clean
        # lookup is a valid result. Shared keys are retried at most once.
        retries = []
        for job, keys in zip(jobs, keys_by_job, strict=True):
            if terms(job, keys):
                continue
            for key in keys:
                wiki, commons = cache[key]
                if key not in fresh_keys and (wiki.get("errors") or commons.get("errors")) and key not in retries:
                    retries.append(key)
        if loop.time() < deadline:
            await phase(retries)

        for key, (wiki, commons) in cache.items():
            if key in fresh_keys:
                continue
            entry = make_entry(key, wiki, commons)
            if entry:
                updates[key] = entry
            elif key in stale and (wiki.get("errors") or commons.get("errors")):
                saved_wiki, saved_commons = restore(stale[key])
                for state, saved in ((wiki, saved_wiki), (commons, saved_commons)):
                    state.update(saved)
                    state["cache_stale"] = True
        diagnostics = []
        for job, keys in zip(jobs, keys_by_job, strict=True):
            states = scene_states(keys)
            job["related_terms"] = terms(job, keys)
            job["related_cache_entries"] = [updates[key] for key in keys if key in updates]
            for (seed, _, _, _), wiki, commons in states:
                if wiki.get("geographic") or commons.get("geographic"):
                    job["related_parent"] = _parent_label(
                        job["queries"], seed, _context(job["queries"], seed)
                    )
                    break
            # Include even suppressed country lookups in error diagnostics.
            all_states = [(key, *cache[key]) for key in keys]
            errors = [
                {"seed": key[0], "provider": provider, **error}
                for key, wiki, commons in all_states
                for provider, state in (("wikidata", wiki), ("commons", commons))
                for error in state.get("errors", [])
            ]
            providers = [
                {
                    "seed": key[0],
                    "provider": provider,
                    "attempts": state.get("attempts", 0),
                    "elapsed_s": state.get("elapsed_s", 0.0),
                    "terms_count": len(state.get("terms", [])),
                }
                for key, wiki, commons in all_states
                for provider, state in (("wikidata", wiki), ("commons", commons))
                if state
            ]
            diagnostic = {
                "seeds": [key[0] for key in keys],
                "cache_hits": [key[0] for key in keys if key in fresh_keys],
                "cache_stale": [key[0] for key, wiki, commons in states
                                if wiki.get("cache_stale") or commons.get("cache_stale")],
                "roots": [root for _, wiki, _ in states for root in wiki.get("roots", [])],
                "commons_roots": [
                    root for _, _, commons in states for root in commons.get("commons_roots", [])
                ],
                "terms_by_origin": {
                    origin: [row["term"] for row in job["related_terms"] if row["origin"] == origin]
                    for origin in ("script", "wikidata", "commons")
                },
                "errors": errors,
                "timeouts": sum(error["error"] == "TimeoutError" for error in errors),
                "providers": providers,
                "elapsed_s": round(
                    sum(
                        max(wiki.get("elapsed_s", 0), commons.get("elapsed_s", 0))
                        for _, wiki, commons in all_states
                    ),
                    3,
                ),
                "batch_elapsed_s": round(loop.time() - started, 3),
                "status": ("ok" if job["related_terms"] else "error" if errors else "no_entity"),
            }
            job["related_diagnostic"] = diagnostic
            diagnostics.append(diagnostic)
        return diagnostics
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _REQUEST_LOCKS.reset(locks_token)
        _RESPONSE_CACHE.reset(response_token)
        _HOSTS.reset(host_token)
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
