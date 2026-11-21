import { loadCatalog, type Clip } from "./catalog";
import { loadJob, requestId, type JobRecord } from "./jobs";

export interface RestEnv {
  CATALOG_URL?: string;
  CATALOG_TTL_SECONDS?: string;
  CATALOG_JOBS_URL?: string;
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_DISPATCH_TOKEN?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
}

type Filters = {
  aspect_ratio?: string | null;
  max_duration?: number;
  min_width?: number;
  fps?: number;
  source?: string;
  language?: string;
  has_audio?: boolean;
  limit?: number;
  cursor?: string;
  import_if_missing?: boolean;
};
type SearchInput = { query: string; filters: Filters };
const filterNames = new Set([
  "aspect_ratio", "max_duration", "min_width", "fps", "source",
  "language", "has_audio", "limit", "cursor", "import_if_missing",
]);
const ratios = new Set(["9:16", "16:9", "1:1", "other"]);
const languages = new Set(["es", "en", "zh", "ru"]);
const json = (body: unknown, status = 200): Response => Response.json(body, {
  status, headers: { "cache-control": "no-store" },
});
const error = (code: string, message: string, status: number) =>
  json({ error: { code, message } }, status);

function parseFilters(raw: unknown): Filters {
  if (raw === undefined || raw === null) return {};
  if (typeof raw !== "object" || Array.isArray(raw)) throw new TypeError("filters must be an object");
  const row = raw as Record<string, unknown>;
  for (const [key, value] of Object.entries(row)) {
    if (!filterNames.has(key)) throw new TypeError("unknown filter " + key);
    if (key === "aspect_ratio" && value !== null && (!value || !ratios.has(String(value)))) {
      throw new TypeError("invalid aspect_ratio");
    }
    if (["max_duration", "fps", "min_width", "limit"].includes(key)) {
      const isInteger = ["min_width", "limit"].includes(key);
      if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 ||
          (isInteger && !Number.isSafeInteger(value)) ||
          (key === "max_duration" && value > 20) || (key === "limit" && value > 100)) {
        throw new TypeError("invalid " + key);
      }
    }
    if (key === "language" && !languages.has(String(value))) throw new TypeError("invalid language");
    if (key === "source" && (typeof value !== "string" || !/^[a-z0-9_]{1,64}$/.test(value))) {
      throw new TypeError("invalid source");
    }
    if (key === "has_audio" && typeof value !== "boolean") throw new TypeError("invalid has_audio");
    if (key === "import_if_missing" && typeof value !== "boolean") {
      throw new TypeError("invalid import_if_missing");
    }
    if (key === "cursor" && (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value))) {
      throw new TypeError("invalid cursor");
    }
  }
  return row as Filters;
}

function parseSearch(body: unknown): SearchInput {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new TypeError("body must be object");
  const obj = body as Record<string, unknown>;
  if (Object.keys(obj).some((key) => !["query", "filters"].includes(key)) ||
      typeof obj.query !== "string" || !obj.query.trim() || obj.query.length > 200) {
    throw new TypeError("query must be nonempty text of at most 200 characters");
  }
  return { query: obj.query.trim(), filters: parseFilters(obj.filters) };
}

function queryUrl(filters: Filters, query: string): string {
  const url = new URL("https://clipoteca.invalid/v1/clips");
  url.searchParams.set("q", query);
  for (const [key, value] of Object.entries(filters)) {
    if (key === "import_if_missing" || value === null || value === undefined) continue;
    url.searchParams.set(key, String(value));
  }
  return url.href;
}

async function searchExisting(env: RestEnv, input: SearchInput): Promise<Clip[]> {
  // Reuse catalogue query semantics, including Unicode normalization and both languages.
  const { handleCatalog } = await import("./catalog");
  const response = await handleCatalog(new Request(queryUrl({ ...input.filters, limit: 100 }, input.query)), env);
  if (!response?.ok) throw new Error("catalogue search unavailable");
  const payload = (await response.json()) as { items: Clip[] };
  return payload.items;
}

async function validateJson(request: Request): Promise<unknown> {
  if (!request.headers.get("content-type")?.toLowerCase().includes("application/json")) {
    throw new TypeError("content-type must be application/json");
  }
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > 16384) throw new TypeError("body exceeds 16 KiB");
  const raw = await request.text();
  if (raw.length > 16384) throw new TypeError("body exceeds 16 KiB");
  try { return JSON.parse(raw) as unknown; }
  catch { throw new TypeError("invalid JSON"); }
}

function bearer(request: Request, token?: string): boolean {
  if (!token || token.length < 16) return false;
  const auth = request.headers.get("authorization");
  return Boolean(auth && auth.startsWith("Bearer ") &&
    auth.slice(7).length === token.length &&
    auth.slice(7).split("").every((c, i) => c.charCodeAt(0) === token.charCodeAt(i)));
}

async function quota(request: Request, scope: string): Promise<boolean> {
  const cache = (caches as CacheStorage & { default: Cache }).default;
  if (!cache) return false;
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
  // Key is a bounded hash so no raw IP leaks into cache URLs.
  const bytes = new TextEncoder().encode(scope + ":" + ip);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const id = [...new Uint8Array(hash)].slice(0, 12).map((n) => n.toString(16).padStart(2, "0")).join("");
  const key = new Request("https://clipoteca.invalid/_quota/" + scope + "/" + id);
  const existing = await cache.match(key);
  if (existing) return false;
  await cache.put(key, new Response("1", { headers: { "cache-control": "public, max-age=15" } }));
  return true;
}

function dispatchFields(input: SearchInput, rid: string): Record<string, string> {
  return {
    query: input.query, request_id: rid,
    aspect_ratio: input.filters.aspect_ratio ?? "",
    max_duration: String(input.filters.max_duration ?? 20),
    max_results: String(Math.min(input.filters.limit ?? 3, 50)),
  };
}

async function dispatchNow(env: RestEnv, input: SearchInput, rid: string): Promise<boolean> {
  const token = env.CLIPOTECA_DISPATCH_TOKEN;
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!token || !/^[\w.-]+\/[\w.-]+$/.test(repo)) return false;
  const result = await fetch(
    "https://api.github.com/repos/" + repo + "/actions/workflows/ingest.yml/dispatches",
    { method: "POST", headers: {
      authorization: "Bearer " + token, accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28", "content-type": "application/json",
      "user-agent": "clipoteca-worker",
    }, body: JSON.stringify({ ref: "main", inputs: dispatchFields(input, rid) }) },
  );
  return result.status === 204;
}

export async function submitImport(
  request: Request, env: RestEnv, input: SearchInput,
): Promise<Response> {
  const dispatchFilters = {
    aspect_ratio: input.filters.aspect_ratio ?? null,
    max_duration: input.filters.max_duration ?? 20,
  };
  const rid = await requestId(input.query, dispatchFilters);
  const old = await loadJob(env, rid);
  if (old?.status === "published" || old?.status === "duplicate") {
    return json({ request_id: rid, status: old.status, job: old });
  }
  if (old && ["searching","fetching","processing","publishing","queued"].includes(old.status)) {
    return json({ request_id: rid, status: old.status, job: old }, 202);
  }
  if (!await quota(request, "import")) return error("rate_limited", "Too many imports", 429);
  if (!await dispatchNow(env, input, rid)) {
    return error("dispatch_unavailable", "Could not schedule ingest", 503);
  }
  return json({ request_id: rid, status: "queued" }, 202);
}

export async function handleRest(request: Request, env: RestEnv): Promise<Response | null> {
  const url = new URL(request.url);
  const path = url.pathname;
  if (request.method === "GET" && /^\/v1\/jobs\//.test(path)) {
    const rid = path.slice("/v1/jobs/".length);
    if (!/^[a-f0-9]{64}$/.test(rid)) return error("invalid_request_id", "Invalid request id", 400);
    try {
      const job = await loadJob(env, rid);
      return json(job ?? { request_id: rid, status: "queued", detail: "unknown" });
    } catch { return error("job_unavailable", "Job storage unavailable", 503); }
  }
  if (request.method !== "POST") return null;
  if (!["/v1/search", "/v1/import", "/v1/contributions"].includes(path)) return null;
  if (path === "/v1/import" && !bearer(request, env.CLIPOTECA_WRITE_TOKEN)) {
    return error("unauthorized", "Bearer token required", 401);
  }
  if (path === "/v1/contributions") {
    return error("not_implemented", "Contribution intake not configured", 503);
  }
  let parsed: SearchInput;
  try { parsed = parseSearch(await validateJson(request)); }
  catch (err) { return error("invalid_input", err instanceof Error ? err.message : "Invalid input", 400); }
  try {
    if (path === "/v1/import") return submitImport(request, env, parsed);
    const items = await searchExisting(env, parsed);
    if (!parsed.filters.import_if_missing || items.length > 0) {
      return json({ clips: items, next_cursor: null, status: "published" });
    }
    const queued = await submitImport(request, env, parsed);
    if (!queued.ok) return queued;
    const result = (await queued.json()) as Record<string, unknown>;
    return json({ clips: items, next_cursor: null, ...result }, queued.status);
  } catch {
    return error("upstream_unavailable", "Catalog or job storage unavailable", 503);
  }
}
