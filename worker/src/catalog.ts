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
}

const DEFAULT_CATALOG =
  "https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/data/clips.json";

export async function loadCatalog(env: CatalogEnv): Promise<Clip[]> {
  const url = env.CATALOG_URL ?? DEFAULT_CATALOG;
  const configured = Number.parseInt(env.CATALOG_TTL_SECONDS ?? "60", 10);
  const ttl = Number.isFinite(configured) && configured >= 0 ? configured : 60;
  const key = new Request(url);
  const cache = (caches as CacheStorage & { default: Cache }).default;
  let response = await cache.match(key);
  if (!response) {
    response = await fetch(key);
    if (!response.ok) throw new Error(`catalog fetch failed: ${response.status}`);
    const stored = new Response(response.body, response);
    stored.headers.set("Cache-Control", `public, max-age=${ttl}`);
    await cache.put(key, stored.clone());
    response = stored;
  }
  const data: unknown = await response.json();
  if (!Array.isArray(data)) throw new Error("catalog response must be an array");
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
    const limit = parseNonNegativeInteger(url.searchParams.get("limit"), 20, false);
    const cursor = parseNonNegativeInteger(url.searchParams.get("cursor"), 0, true);
    if (limit === null || limit > 100 || cursor === null) {
      return json({ error: { code: "invalid_pagination", message: "limit must be 1..100; cursor a nonnegative integer" } }, 400);
    }
    const clips = await loadCatalog(env);
    const filtered = clips.filter((clip) => matches(clip, url.searchParams));
    const items = filtered.slice(cursor, cursor + limit);
    const nextCursor = cursor + limit < filtered.length ? String(cursor + limit) : null;
    return json({ items, next_cursor: nextCursor });
  }
  const match = url.pathname.match(/^\/v1\/clips\/(clip-\d{6})(\/download)?$/);
  if (!match) return null;
  const clips = await loadCatalog(env);
  const clip = clips.find((item) => item.id === match[1]);
  if (!clip) return json({ error: { code: "not_found", message: "clip not found" } }, 404);
  if (match[2]) return Response.redirect(clip.storage.asset_url, 302);
  return json(clip);
}
