export type Clip = {
  id: string;
  title: Record<"es" | "en", string>;
  description: Record<"es" | "en", string>;
  tags: Record<"es" | "en", string[]>;
  source: {
    provider: string;
    source_language: string;
    source_title?: string;
    keywords?: string[];
  };
  media: {
    aspect_ratio: string;
    duration_seconds: number;
    width: number;
    fps: number;
    has_audio: boolean;
  };
  storage: { asset_url: string };
};

export interface CatalogEnv {
  CATALOG_URL?: string;
  CATALOG_TTL_SECONDS?: string;
  CLIPOTECA_GITHUB_REPO?: string;
}

const DEFAULT_REPOSITORY = "riquelmechile/clipoteca-comunitaria";
const REPO_PATTERN = /^[\w.-]+\/[\w.-]+$/;

function decodeCatalog(content: string): unknown {
  const binary = atob(content.replace(/\s+/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as unknown;
}

export async function loadCatalog(env: CatalogEnv): Promise<Clip[]> {
  const repo = env.CLIPOTECA_GITHUB_REPO || DEFAULT_REPOSITORY;
  if (!REPO_PATTERN.test(repo)) throw new Error("invalid catalogue repository");
  const url = env.CATALOG_URL ??
    "https://api.github.com/repos/" + repo + "/contents/data/clips.json?ref=main";
  const configured = Number.parseInt(env.CATALOG_TTL_SECONDS ?? "10", 10);
  const ttl = Number.isFinite(configured) && configured >= 0
    ? Math.min(30, configured) : 10;
  const key = new Request(url);
  const cache = (caches as CacheStorage & { default: Cache }).default;
  let response = cache && ttl > 0 ? await cache.match(key) : undefined;
  if (!response) {
    response = await fetch(key, { headers: {
      accept: "application/vnd.github+json",
      "cache-control": "no-cache",
      "user-agent": "clipoteca-worker",
    } });
    if (!response.ok) throw new Error("catalog fetch unavailable");
    if (cache && ttl > 0) {
      const stored = new Response(response.body, response);
      stored.headers.set("Cache-Control", "public, max-age=" + ttl);
      await cache.put(key, stored.clone());
      response = stored;
    }
  }
  let data: unknown = await response.json();
  if (data && typeof data === "object" && !Array.isArray(data) &&
      "encoding" in data && (data as {encoding?: unknown}).encoding === "base64") {
    const encoded = (data as {content?: unknown}).content;
    if (typeof encoded !== "string") throw new Error("malformed catalogue");
    data = decodeCatalog(encoded);
  }
  if (!Array.isArray(data)) throw new Error("catalogue must be an array");
  return data as Clip[];
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "public, max-age=30" },
  });
}

// Match Python's NFKC + casefold + accent folding for catalogue search.
export function normalizeSearch(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("und")
    .replace(/ß/g, "ss")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "");
}

export function searchTokens(value: string): string[] {
  const words = normalizeSearch(value).match(/[\p{L}\p{N}_]+/gu) ?? [];
  const tokens = new Set(words);
  for (const word of words) {
    if (/[\u3400-\u9fff]/u.test(word)) {
      const chars = [...word];
      for (let i = 0; i + 1 < chars.length; i++) {
        tokens.add(chars.slice(i, i + 2).join(""));
      }
    }
  }
  return [...tokens];
}

function matches(clip: Clip, params: URLSearchParams): boolean {
  const query = searchTokens(params.get("q") ?? "");
  const texts = [
    ...Object.values(clip.title),
    ...Object.values(clip.description),
    ...clip.tags.es,
    ...clip.tags.en,
    clip.source.source_title ?? "",
    ...(clip.source.keywords ?? []),
  ];
  const document = searchTokens(texts.join(" "));
  const vocabulary = new Set(document);
  return (
    query.every((term) => vocabulary.has(term)) &&
    (!params.has("aspect_ratio") || clip.media.aspect_ratio === params.get("aspect_ratio")) &&
    (!params.has("max_duration") || clip.media.duration_seconds <= Number(params.get("max_duration"))) &&
    (!params.has("min_width") || clip.media.width >= Number(params.get("min_width"))) &&
    (!params.has("fps") || clip.media.fps >= Number(params.get("fps"))) &&
    (!params.has("source") || clip.source.provider === params.get("source")) &&
    (!params.has("language") || clip.source.source_language === params.get("language")) &&
    (!params.has("has_audio") || clip.media.has_audio === (params.get("has_audio") === "true"))
  );
}

function parseNonNegativeInteger(raw: string | null, fallback: number, allowZero: boolean): number | null {
  if (raw === null) return fallback;
  if (!/^(0|[1-9]\d*)$/.test(raw)) return null;
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || (!allowZero && parsed === 0)) return null;
  return parsed;
}

export async function handleCatalog(request: Request, env: CatalogEnv): Promise<Response | null> {
  const url = new URL(request.url);
  if (request.method !== "GET") return null;
  if (url.pathname === "/v1/clips") {
    const allowed = new Set(["q", "aspect_ratio", "max_duration", "min_width",
      "fps", "source", "language", "has_audio", "limit", "cursor"]);
    let invalidKey = false;
    url.searchParams.forEach((value, key) => {
      if (!allowed.has(key) || value.length > 200) invalidKey = true;
    });
    if (invalidKey) return json({error:{code:"invalid_filter",message:"invalid filter"}}, 400);
    for (const name of ["max_duration", "min_width", "fps"]) {
      if (url.searchParams.has(name) &&
          (!Number.isFinite(Number(url.searchParams.get(name))) ||
          Number(url.searchParams.get(name)) <= 0 ||
          (name === "max_duration" && Number(url.searchParams.get(name)) > 20)))
        return json({error:{code:"invalid_filter",message:"invalid "+name}}, 400);
    }
    if (url.searchParams.has("aspect_ratio") &&
        !["9:16","16:9","1:1","other"].includes(url.searchParams.get("aspect_ratio") ?? ""))
      return json({error:{code:"invalid_filter",message:"invalid aspect_ratio"}}, 400);
    if (url.searchParams.has("language") &&
        !["es","en","zh","ru"].includes(url.searchParams.get("language") ?? ""))
      return json({error:{code:"invalid_filter",message:"invalid language"}}, 400);
    if (url.searchParams.has("has_audio") &&
        !["true","false"].includes(url.searchParams.get("has_audio") ?? ""))
      return json({error:{code:"invalid_filter",message:"invalid has_audio"}}, 400);
    const limit = parseNonNegativeInteger(url.searchParams.get("limit"), 20, false);
    const cursor = parseNonNegativeInteger(url.searchParams.get("cursor"), 0, true);
    if (limit === null || limit > 100 || cursor === null) {
      return json({ error: { code: "invalid_pagination", message: "limit must be 1..100; cursor a nonnegative integer" } }, 400);
    }
    let clips: Clip[];
    try { clips = await loadCatalog(env); }
    catch { return json({error:{code:"catalog_unavailable",message:"Catalogue unavailable"}}, 503); }
    const filtered = clips.filter((clip) => matches(clip, url.searchParams));
    const items = filtered.slice(cursor, cursor + limit);
    const nextCursor = cursor + limit < filtered.length ? String(cursor + limit) : null;
    return json({ items, next_cursor: nextCursor });
  }
  const match = url.pathname.match(/^\/v1\/clips\/(clip-\d{6})(\/download)?$/);
  if (!match) return null;
  let clips: Clip[];
  try { clips = await loadCatalog(env); }
  catch { return json({error:{code:"catalog_unavailable",message:"Catalogue unavailable"}}, 503); }
  const clip = clips.find((item) => item.id === match[1]);
  if (!clip) return json({ error: { code: "not_found", message: "clip not found" } }, 404);
  if (match[2]) return Response.redirect(clip.storage.asset_url, 302);
  return json(clip);
}
