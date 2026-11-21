/** Durable GitHub job reader and deterministic request keys. */
import { normalizeSearch } from "./catalog";

export type JobStatus = "queued" | "searching" | "fetching" | "processing" | "publishing"
  | "published" | "no_results" | "duplicate" | "failed" | "rate_limited" | "unavailable" | "dead_letter";
export type JobRecord = {
  request_id: string; status: JobStatus; attempt?: number; updated_at?: string;
  [key: string]: unknown;
};
export interface JobEnv { CATALOG_JOBS_URL?: string; CLIPOTECA_GITHUB_REPO?: string; }

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  const object = value as Record<string, unknown>;
  return "{" + Object.keys(object).sort().map(
    (key) => JSON.stringify(key) + ":" + canonicalJson(object[key]),
  ).join(",") + "}";
}

export async function requestId(query: string, filters: Record<string, unknown>, horizon = "v1"): Promise<string> {
  const normalized = { ...filters };
  if (normalized.aspect_ratio === "") normalized.aspect_ratio = null;
  if (normalized.max_duration !== undefined && normalized.max_duration !== null &&
      normalized.max_duration !== "") {
    const amount = Number(normalized.max_duration);
    if (!Number.isFinite(amount)) throw new TypeError("invalid max_duration");
    normalized.max_duration = amount;
  }
  const body = canonicalJson({
    query: normalizeSearch(query).replace(/\s+/gu, " ").trim(),
    filters: normalized,
    horizon,
  });
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
  return [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, "0")).join("");
}

const STATUSES = new Set(["queued", "searching", "fetching", "processing", "publishing",
  "published", "no_results", "duplicate", "failed", "rate_limited", "unavailable", "dead_letter"]);
const REPO_PATTERN = /^[\w.-]+\/[\w.-]+$/;

function decodeBase64(content: string): string {
  const binary = atob(content.replace(/\s+/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

export async function loadJob(env: JobEnv, id: string): Promise<JobRecord | null> {
  if (!/^[a-f0-9]{64}$/.test(id)) throw new TypeError("invalid request_id");
  const repo = env.CLIPOTECA_GITHUB_REPO || "riquelmechile/clipoteca-comunitaria";
  if (!REPO_PATTERN.test(repo)) throw new TypeError("invalid repository");
  const prefix = env.CATALOG_JOBS_URL;
  const address = prefix
    ? prefix.replace(/\/?$/, "/") + id + ".json"
    : "https://api.github.com/repos/" + repo + "/contents/data/jobs/" + id + ".json?ref=main";
  const response = await fetch(address, {
    headers: { accept: "application/vnd.github+json", "cache-control": "no-cache",
      "user-agent": "clipoteca-worker" },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("job storage unavailable");
  let data: unknown = await response.json();
  if (data && typeof data === "object" && "encoding" in data &&
      (data as { encoding?: unknown }).encoding === "base64") {
    const encoded = (data as { content?: unknown }).content;
    if (typeof encoded !== "string") throw new Error("invalid Github job response");
    data = JSON.parse(decodeBase64(encoded)) as unknown;
  }
  if (!data || typeof data !== "object") throw new Error("malformed job");
  const record = data as JobRecord;
  if (record.request_id !== id || !STATUSES.has(record.status)) throw new Error("invalid job");
  return record;
}

export function isStaleJob(job: JobRecord, ageMs = 45 * 60_000): boolean {
  if (!["queued", "searching", "fetching", "processing", "publishing"].includes(job.status)) return false;
  const updated = typeof job.updated_at === "string" ? Date.parse(job.updated_at) : NaN;
  return !Number.isFinite(updated) || Date.now() - updated > ageMs;
}

export function expiredDeadLetter(job: JobRecord): boolean {
  if (job.status !== "dead_letter") return false;
  const updated = typeof job.updated_at === "string" ? Date.parse(job.updated_at) : NaN;
  return Number.isFinite(updated) && Date.now() - updated >= 24 * 60 * 60_000;
}
