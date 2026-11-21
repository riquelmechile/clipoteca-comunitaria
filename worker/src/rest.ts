import { loadCatalog, type Clip } from "./catalog";
import { loadJob, requestId, expiredDeadLetter, isStaleJob } from "./jobs";
import { dispatchIngest } from "./dispatch";
import { submitContribution } from "./contributions";

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
  const unknown = Object.keys(obj).find((key) => !["query", "filters"].includes(key));
  if (unknown) {
    // Identify the invalid field without reflecting an unbounded user-controlled key.
    throw new TypeError("unknown body field: " + unknown.slice(0, 64) +
      "; filter options belong under filters");
  }
  if (typeof obj.query !== "string" || !obj.query.trim() || obj.query.length > 200) {
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

async function searchExisting(env: RestEnv, input: SearchInput):
  Promise<{ clips: Clip[]; next_cursor: string | null }> {
  const { handleCatalog } = await import("./catalog");
  const response = await handleCatalog(new Request(queryUrl(input.filters, input.query)), env);
  if (!response?.ok) throw new Error("catalogue search unavailable");
  const payload = (await response.json()) as { items: Clip[]; next_cursor: string | null };
  return { clips: payload.items, next_cursor: payload.next_cursor };
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

// The Worker has no durable queued record until the workflow starts. Keep
// a short-lived marker *only after* a successful dispatch, so an identical
// import is idempotent even when repeated before Python writes its first job.
function recentImportKey(id: string): Request {
  return new Request("https://clipoteca.invalid/_import/" + id);
}
async function recentlyDispatched(id: string): Promise<boolean> {
  try {
    const cache = (caches as CacheStorage & { default: Cache }).default;
    return Boolean(cache && await cache.match(recentImportKey(id)));
  } catch { return false; }
}
async function rememberDispatch(id: string): Promise<void> {
  try {
    const cache = (caches as CacheStorage & { default: Cache }).default;
    if (cache) {
      await cache.put(recentImportKey(id),
        new Response("queued", { headers: { "cache-control": "public, max-age=30" } }));
    }
  } catch { /* A cache failure must not turn an accepted dispatch into a 503. */ }
}

export async function submitImport(
  request: Request, env: RestEnv, input: SearchInput,
): Promise<Response> {
  // All content and pagination filters must participate in the deterministic key.
  // import_if_missing controls queuing, not the content identity.
  const { import_if_missing: _enqueue, ...requestedFilters } = input.filters;
  const dispatchFilters = {
    ...requestedFilters,
    aspect_ratio: input.filters.aspect_ratio ?? null,
    max_duration: input.filters.max_duration ?? 20,
  };
  const rid = await requestId(input.query, dispatchFilters);
  const old = await loadJob(env, rid);
  if (old?.status === "dead_letter" && !expiredDeadLetter(old)) {
    return error("dead_letter", "Retry budget exhausted; try after 24 hours", 409);
  }
  if (old?.status === "no_results") {
    return json({ request_id: rid, status: "no_results", job: old });
  }
  if (old?.status === "published" || old?.status === "duplicate") {
    return json({ request_id: rid, status: old.status, job: old });
  }
  if (old && ["searching","fetching","processing","publishing","queued"].includes(old.status)
      && !isStaleJob(old)) {
    return json({ request_id: rid, status: old.status, job: old }, 202);
  }
  if (await recentlyDispatched(rid)) {
    return json({ request_id: rid, status: "queued" }, 202);
  }
  if (!await quota(request, "import")) return error("rate_limited", "Too many imports", 429);
  // The Python workflow must receive the identical filter set hashed above;
  // omitting a default (max_duration=20) caused the live request_id mismatch.
  const dispatched = await dispatchIngest(request, env,
    { ...input, filters: dispatchFilters }, rid);
  if (!dispatched.ok) {
    return error(dispatched.code, dispatched.message, dispatched.status);
  }
  await rememberDispatch(rid);
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
      return job ? json(job) : error("job_not_found", "Job does not exist", 404);
    } catch { return error("job_unavailable", "Job storage unavailable", 503); }
  }
  if (request.method !== "POST") return null;
  if (!["/v1/search", "/v1/import", "/v1/contributions"].includes(path)) return null;
  if (path === "/v1/import" && !bearer(request, env.CLIPOTECA_WRITE_TOKEN)) {
    return error("unauthorized", "Bearer token required", 401);
  }
  if (path === "/v1/contributions") return submitContribution(request, env);
  let parsed: SearchInput;
  try { parsed = parseSearch(await validateJson(request)); }
  catch (err) { return error("invalid_input", err instanceof Error ? err.message : "Invalid input", 400); }
  try {
    if (path === "/v1/import") return submitImport(request, env, parsed);
    const page = await searchExisting(env, parsed);
    if (!parsed.filters.import_if_missing || page.clips.length > 0) {
      return json({ ...page, status: "published" });
    }
    // A cursor past the last page is not an empty catalogue.
    const first = await searchExisting(env, {
      ...parsed, filters: { ...parsed.filters, cursor: "0", limit: 1 },
    });
    if (first.clips.length) return json({ ...page, status: "published" });
    const queued = await submitImport(request, env, parsed);
    if (!queued.ok) return queued;
    const result = (await queued.json()) as Record<string, unknown>;
    return json({ ...page, ...result }, queued.status);
  } catch {
    return error("upstream_unavailable", "Catalog or job storage unavailable", 503);
  }
}
