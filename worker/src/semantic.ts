/** M22 multilingual CLIP retrieval. Never runs models in the Worker. */

import { loadCatalog, normalizeSearch, searchTokens, type CatalogEnv, type Clip } from "./catalog";
import { dispatchIngest, type DispatchEnv } from "./dispatch";
import { requestId } from "./jobs";

const DIM = 512;
const WIDTH = DIM * 2;
const SHARD_MAX = 4096;
const MAX_SHARDS = 32; // reserve subrequests for catalogue, query and dispatch
const ROOT = "https://raw.githubusercontent.com/";
const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const SHARD_FILE = /^shards\/shard-[0-9]{6}-[a-f0-9]{64}\.f16$/;
const ID = /^clip-[0-9]{6}$/;
const DIGEST = /^[a-f0-9]{64}$/;

export interface SemanticEnv extends CatalogEnv, DispatchEnv {
  QUERY_ENCODER_URL?: string;
  CLIPOTECA_QUERY_DISPATCH_MODE?: string;
}

export type SemanticStatus = "ready" | "pending" | "unavailable";
export interface SemanticSearchResult {
  clips: Clip[];
  next_cursor: string | null;
  semantic: SemanticStatus;
  scores?: Record<string, number>;
}

interface Shard {
  ids: string[];
  count: number;
  path: string;
  sha256: string;
  blob_sha: string;
}
interface Manifest {
  version: number;
  dimension: number;
  dtype: string;
  shard_capacity: number;
  shards: Shard[];
}

const memoryCache = new Map<string, { until: number; value: Uint8Array }>();
export function resetSemanticCache(): void { memoryCache.clear(); }

function repoName(env: SemanticEnv): string {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!REPO.test(repo)) throw new Error("invalid semantic repository");
  return repo;
}

export function normalizedQuery(query: string): string {
  return query.normalize("NFKC").toLowerCase().replace(/ß/g, "ss").replace(/\s+/gu, " ").trim();
}
export async function sha256(data: Uint8Array | string): Promise<string> {
  const input = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(input));
  return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, "0")).join("");
}

export function halfFloat(bits: number): number {
  const sign = bits & 0x8000 ? -1 : 1;
  const exponent = (bits >>> 10) & 0x1f;
  const mantissa = bits & 0x3ff;
  if (exponent === 31) return mantissa ? Number.NaN : sign * Infinity;
  if (exponent === 0) return sign * mantissa * 2 ** -24;
  return sign * (1 + mantissa / 1024) * 2 ** (exponent - 15);
}

export function decodeFloat16(raw: Uint8Array, offset = 0): Float32Array {
  if (offset < 0 || offset + WIDTH > raw.byteLength) throw new Error("truncated float16");
  const output = new Float32Array(DIM);
  let norm = 0;
  for (let i = 0; i < DIM; i++) {
    const start = offset + i * 2;
    const value = halfFloat(raw[start]! | (raw[start + 1]! << 8));
    if (!Number.isFinite(value)) throw new Error("nonfinite embedding");
    output[i] = value;
    norm += value * value;
  }
  if (norm < 0.98 ** 2 || norm > 1.02 ** 2) throw new Error("invalid embedding norm");
  const length = Math.sqrt(norm);
  for (let i = 0; i < DIM; i++) output[i] = output[i]! / length;
  return output;
}

export function normalizeEmbedding(input: unknown): Float32Array {
  if (!Array.isArray(input) || input.length !== DIM) throw new Error("invalid vector length");
  const data = new Float32Array(DIM);
  let norm = 0;
  for (let i = 0; i < DIM; i++) {
    const n = input[i];
    if (typeof n !== "number" || !Number.isFinite(n)) throw new Error("invalid vector element");
    data[i] = n;
    norm += n * n;
  }
  if (!Number.isFinite(norm) || norm < 1e-10) throw new Error("zero norm");
  for (let i = 0; i < DIM; i++) data[i] = data[i]! / Math.sqrt(norm);
  return data;
}

export function cosine(a: Float32Array, b: Float32Array): number {
  if (a.length !== DIM || b.length !== DIM) throw new Error("invalid embedding dimension");
  let score = 0;
  for (let i = 0; i < DIM; i++) score += a[i]! * b[i]!;
  return Math.max(-1, Math.min(1, score));
}

export function rrfFuse(
  lexical: string[], semantic: { id: string; cosine: number }[],
  k = 60,
): { id: string; combined_score: number }[] {
  const scores = new Map<string, number>();
  for (const [rank, id] of lexical.entries()) {
    scores.set(id, (scores.get(id) ?? 0) + 1 / (k + rank + 1));
  }
  for (const [rank, row] of semantic.entries()) {
    scores.set(row.id, (scores.get(row.id) ?? 0) + 1 / (k + rank + 1));
  }
  const rank = new Map([...lexical, ...semantic.map(x => x.id)].map((id, i) => [id, i]));
  return [...scores].map(([id, combined_score]) => ({id, combined_score}))
    .sort((a, b) => b.combined_score - a.combined_score ||
      (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity) ||
      a.id.localeCompare(b.id));
}

async function bytes(url: string, cap: number, ttlSeconds: number): Promise<Uint8Array | null> {
  const now = Date.now();
  const found = memoryCache.get(url);
  if (found && found.until > now) return found.value;
  const req = new Request(url);
  const cache = typeof caches !== "undefined" ?
    (caches as CacheStorage & { default?: Cache }).default : undefined;
  let response = cache && ttlSeconds > 0 ? await cache.match(req) : undefined;
  if (!response) {
    response = await fetch(url, {
      headers: { accept: "application/octet-stream", "user-agent": "clipoteca-worker" },
      redirect: "error",
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error("embedding fetch unavailable");
  }
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > cap) throw new Error("embedding response too large");
  const raw = new Uint8Array(await response.arrayBuffer());
  if (raw.length > cap) throw new Error("embedding bytes too large");
  if (memoryCache.size >= 16) {
    const oldest = memoryCache.keys().next().value;
    if (oldest) memoryCache.delete(oldest);
  }
  memoryCache.set(url, { value: raw, until: now + ttlSeconds * 1000 });
  if (cache && ttlSeconds > 0) {
    const stored = new Response(raw, { headers: {"cache-control": "public, max-age=" + ttlSeconds }});
    try { await cache.put(req, stored); } catch { /* isolate memory is enough */ }
  }
  return raw;
}
function path(env: SemanticEnv, relative: string): string {
  return ROOT + repoName(env) + "/main/data/embeddings/" + relative;
}

function validatedManifest(value: unknown): Manifest {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid manifest");
  const row = value as Manifest;
  if (row.version !== 2 || row.dimension !== DIM || row.dtype !== "float16-le" ||
      row.shard_capacity !== SHARD_MAX || !Array.isArray(row.shards) ||
      row.shards.length > MAX_SHARDS) throw new Error("invalid manifest contract");
  const seen = new Set<string>();
  for (const [index, shard] of row.shards.entries()) {
    if (!shard || !SHARD_FILE.test(shard.path) ||
        !shard.path.startsWith("shards/shard-" + String(index).padStart(6, "0") + "-") ||
        !DIGEST.test(shard.sha256) || !/^[a-f0-9]{40}$/.test(shard.blob_sha) ||
        !shard.path.endsWith(shard.sha256 + ".f16") ||
        !Number.isInteger(shard.count) || shard.count <= 0 || shard.count > SHARD_MAX ||
        !Array.isArray(shard.ids) || shard.ids.length !== shard.count) {
      throw new Error("invalid shard entry");
    }
    for (const id of shard.ids) {
      if (typeof id !== "string" || !ID.test(id) || seen.has(id)) {
        throw new Error("duplicate or invalid clip ID");
      }
      seen.add(id);
    }
  }
  return row;
}

async function loadManifest(env: SemanticEnv): Promise<Manifest | null> {
  const raw = await bytes(path(env, "manifest.json"), 1_000_000, 10);
  if (raw === null) return null;
  return validatedManifest(JSON.parse(new TextDecoder().decode(raw)));
}

async function loadLegacy(env: SemanticEnv): Promise<Map<string, Float32Array>> {
  const result = new Map<string, Float32Array>();
  const raw = await bytes(path(env, "index.json"), 1_000_000, 10);
  if (!raw) return result;
  const doc = JSON.parse(new TextDecoder().decode(raw)) as {
    dimension?: number; dtype?: string; vectors?: Record<string, {path: string; sha256: string}>;
  };
  if (doc.dimension !== DIM || doc.dtype !== "float16-le" || !doc.vectors) {
    throw new Error("invalid legacy index");
  }
  // Transitional fallback only: migrate larger collections to avoid N
  // subrequests and Cloudflare's per-invocation fetch limit.
  const entries = Object.entries(doc.vectors);
  if (entries.length > 35) throw new Error("legacy index requires migration");
  for (const [id, meta] of entries) {
    if (!ID.test(id) || meta.path !== id + ".f16" || !DIGEST.test(meta.sha256)) {
      throw new Error("invalid legacy vector path");
    }
    const value = await bytes(path(env, id + ".f16"), WIDTH, 60);
    if (!value || await sha256(value) !== meta.sha256) throw new Error("legacy vector mismatch");
    result.set(id, decodeFloat16(value));
  }
  return result;
}

export async function loadCorpus(env: SemanticEnv): Promise<Map<string, Float32Array>> {
  const manifest = await loadManifest(env);
  if (manifest === null) return loadLegacy(env);
  const results = new Map<string, Float32Array>();
  for (const shard of manifest.shards) {
    const blob = await bytes(path(env, shard.path), SHARD_MAX * WIDTH, 3600);
    if (!blob || blob.length !== shard.count * WIDTH ||
        await sha256(blob) !== shard.sha256) throw new Error("semantic shard invalid");
    shard.ids.forEach((id, index) => results.set(id, decodeFloat16(blob, index * WIDTH)));
  }
  return results;
}

function safeEncoderUrl(value: string): URL | null {
  try {
    const u = new URL(value);
    const host = u.hostname.toLowerCase();
    if (u.protocol !== "https:" || u.username || u.password ||
        u.port && u.port !== "443" ||
        !/^[a-z0-9.-]+$/.test(host) || !host.includes(".") ||
        host === "localhost" || host.endsWith(".localhost") ||
        host.endsWith(".local") || host.endsWith(".internal") ||
        host.endsWith(".test") || /^\d+[.\d]*$/.test(host) ||
        host.startsWith("127.") || host.startsWith("10.") ||
        host.startsWith("169.254.") || host.startsWith("192.168.") ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
        u.hash) return null;
    return u;
  } catch { return null; }
}

export async function queryVector(
  env: SemanticEnv, query: string,
): Promise<Float32Array | null> {
  const normalized = normalizedQuery(query);
  if (!normalized || normalized.length > 200) return null;
  const digest = await sha256(normalized);
  const raw = await bytes(path(env, "queries/" + digest + ".json"), 8192, 10);
  if (raw) {
    const payload = JSON.parse(new TextDecoder().decode(raw)) as {
      version: number; dimension: number; dtype: string;
      query_sha256: string; vector: string;
    };
    if (payload.version !== 1 || payload.dimension !== DIM ||
        payload.dtype !== "float16-le" || payload.query_sha256 !== digest ||
        typeof payload.vector !== "string" || payload.vector.length > 1600) {
      throw new Error("invalid cached query vector");
    }
    const binary = atob(payload.vector);
    return decodeFloat16(Uint8Array.from(binary, c => c.charCodeAt(0)));
  }
  if (env.QUERY_ENCODER_URL) {
    const endpoint = safeEncoderUrl(env.QUERY_ENCODER_URL);
    if (!endpoint) throw new Error("unsafe query encoder URL");
    const response = await fetch(endpoint.href, {
      method: "POST", redirect: "error",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({query: normalized}),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok || Number(response.headers.get("content-length")) > 16384)
      throw new Error("query encoder unavailable");
    const body = await response.text();
    if (body.length > 16384) throw new Error("oversized query embedding");
    return normalizeEmbedding((JSON.parse(body) as {vector?: unknown}).vector);
  }
  return null;
}

const queryDispatch = new Map<string, number>();
export function resetQueryDispatch(): void { queryDispatch.clear(); }

export async function queueQuery(
  request: Request, env: SemanticEnv, query: string,
): Promise<boolean> {
  const normalized = normalizedQuery(query);
  const key = await sha256(normalized);
  const next = queryDispatch.get(key) ?? 0;
  if (next > Date.now()) return true;
  // With M22's optional YAML mode, use lightweight query-only dispatch.
  // Before YAML is installed, use existing ingest.yml inputs unchanged.
  if (env.CLIPOTECA_QUERY_DISPATCH_MODE === "1") {
    const repo = repoName(env);
    const token = env.CLIPOTECA_DISPATCH_TOKEN;
    if (!token) return false;
    try {
      const response = await fetch(
        "https://api.github.com/repos/" + repo + "/actions/workflows/ingest.yml/dispatches",
        {method: "POST", redirect: "error", headers: {
          authorization: "Bearer " + token,
          accept: "application/vnd.github+json",
          "content-type": "application/json",
          "user-agent": "clipoteca-worker",
        },body: JSON.stringify({ref: "main", inputs: {
          query: normalized, mode: "query_embedding",
        }})},
      );
      if (response.status !== 204) return false;
      queryDispatch.set(key, Date.now() + 30000);
      return true;
    } catch { return false; }
  }
  const rid = await requestId(normalized, {aspect_ratio: null, max_duration: 20});
  const sent = await dispatchIngest(request, env, {
    query: normalized, filters: {aspect_ratio: null, max_duration: 20},
  }, rid);
  if (sent.ok) queryDispatch.set(key, Date.now() + 30000);
  return sent.ok;
}

function matchesFilters(clip: Clip, params: URLSearchParams): boolean {
  return (
    (!params.has("aspect_ratio") || clip.media.aspect_ratio === params.get("aspect_ratio")) &&
    (!params.has("max_duration") || clip.media.duration_seconds <= Number(params.get("max_duration"))) &&
    (!params.has("min_width") || clip.media.width >= Number(params.get("min_width"))) &&
    (!params.has("fps") || clip.media.fps >= Number(params.get("fps"))) &&
    (!params.has("source") || clip.source.provider === params.get("source")) &&
    (!params.has("language") || clip.source.source_language === params.get("language")) &&
    (!params.has("has_audio") || clip.media.has_audio === (params.get("has_audio") === "true"))
  );
}
function keywordMatches(clip: Clip, query: string): boolean {
  const vocabulary = new Set(searchTokens([
    ...Object.values(clip.title),
    ...Object.values(clip.description),
    ...clip.tags.es, ...clip.tags.en, clip.source.source_title ?? "",
    ...(clip.source.keywords ?? []),
  ].join(" ")));
  return searchTokens(query).every(t => vocabulary.has(t));
}

export async function semanticSearch(
  request: Request, env: SemanticEnv, query: string, filters: URLSearchParams,
): Promise<SemanticSearchResult> {
  const clips = await loadCatalog(env);
  const eligible = clips.filter(clip => matchesFilters(clip, filters));
  const lexical = eligible.filter(clip => keywordMatches(clip, query));
  const cursor = Number(filters.get("cursor") ?? 0);
  const limit = Number(filters.get("limit") ?? 20);
  const oldPage = () => ({
    clips: lexical.slice(cursor, cursor + limit),
    next_cursor: cursor + limit < lexical.length ? String(cursor + limit) : null,
  });
  try {
    const corpus = await loadCorpus(env);
    if (!corpus.size) return {...oldPage(), semantic: "unavailable"};
    const vector = await queryVector(env, query);
    if (!vector) {
      const queued = await queueQuery(request, env, query);
      return {...oldPage(), semantic: queued ? "pending" : "unavailable"};
    }
    const scored = eligible.filter(clip => corpus.has(clip.id)).map(clip => ({
      id: clip.id, cosine: cosine(vector, corpus.get(clip.id)!),
    })).sort((a, b) => b.cosine - a.cosine || a.id.localeCompare(b.id));
    const fused = rrfFuse(lexical.map(c => c.id), scored);
    const byId = new Map(eligible.map(c => [c.id, c]));
    const selected = fused.slice(cursor, cursor + limit);
    return {
      clips: selected.map(row => byId.get(row.id)!).filter(Boolean),
      next_cursor: cursor + limit < fused.length ? String(cursor + limit) : null,
      semantic: "ready", scores: Object.fromEntries(
        selected.map(row => [row.id, row.combined_score])
      ),
    };
  } catch {
    // Never change existing keyword behavior if a model, cache, shard or
    // configured endpoint fails. No remote exception bodies are disclosed.
    return {...oldPage(), semantic: "unavailable"};
  }
}
