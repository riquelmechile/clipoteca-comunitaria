export type Clip = {
  id: string;
  title: Record<"es" | "en", string>;
  description: Record<"es" | "en", string>;
  tags: Record<"es" | "en", string[]>;
  source: { provider: string; source_language: string };
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
  CATALOG_CACHE_TTL?: string;
}

const DEFAULT_CATALOG =
  "https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/data/clips.json";

export async function loadCatalog(env: CatalogEnv): Promise<Clip[]> {
  const url = env.CATALOG_URL ?? DEFAULT_CATALOG;
  const ttl = Number.parseInt(env.CATALOG_CACHE_TTL ?? "60", 10);
  const key = new Request(url);
  const cache = caches.default;
  let response = await cache.match(key);
  if (!response) {
    response = await fetch(key);
    if (!response.ok) throw new Error(`catalog fetch failed: ${response.status}`);
    const stored = new Response(response.body, response);
    stored.headers.set("Cache-Control", `public, max-age=${Math.max(0, ttl)}`);
    await cache.put(key, stored.clone());
    response = stored;
  }
  return (await response.json()) as Clip[];
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "public, max-age=30" } });
}

function matches(clip: Clip, params: URLSearchParams): boolean {
  const q = params.get("q")?.toLocaleLowerCase();
  const text = [
    ...Object.values(clip.title),
    ...Object.values(clip.description),
    ...clip.tags.es,
    ...clip.tags.en,
  ]
    .join(" ")
    .toLocaleLowerCase();
  return (
    (!q || text.includes(q)) &&
    (!params.has("aspect_ratio") || clip.media.aspect_ratio === params.get("aspect_ratio")) &&
    (!params.has("max_duration") || clip.media.duration_seconds <= Number(params.get("max_duration"))) &&
    (!params.has("min_width") || clip.media.width >= Number(params.get("min_width"))) &&
    (!params.has("fps") || clip.media.fps >= Number(params.get("fps"))) &&
    (!params.has("source") || clip.source.provider === params.get("source")) &&
    (!params.has("language") || clip.source.source_language === params.get("language")) &&
    (!params.has("has_audio") || clip.media.has_audio === (params.get("has_audio") === "true"))
  );
}

export async function handleCatalog(request: Request, env: CatalogEnv): Promise<Response | null> {
  const url = new URL(request.url);
  if (request.method !== "GET") return null;
  const clips = await loadCatalog(env);
  if (url.pathname === "/v1/clips") {
    const limit = Math.min(100, Math.max(1, Number(url.searchParams.get("limit") ?? 20)));
    const cursor = Math.max(0, Number(url.searchParams.get("cursor") ?? 0));
    const filtered = clips.filter((clip) => matches(clip, url.searchParams));
    const items = filtered.slice(cursor, cursor + limit);
    const nextCursor = cursor + limit < filtered.length ? String(cursor + limit) : null;
    return json({ items, next_cursor: nextCursor });
  }
  const match = url.pathname.match(/^\/v1\/clips\/(clip-\d{6})(\/download)?$/);
  if (!match) return null;
  const clip = clips.find((item) => item.id === match[1]);
  if (!clip) return json({ error: { code: "not_found", message: "clip not found" } }, 404);
  if (match[2]) return Response.redirect(clip.storage.asset_url, 302);
  return json(clip);
}
