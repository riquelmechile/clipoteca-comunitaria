import { normalizeSearch } from "./catalog";

export type JobStatus = "queued" | "searching" | "fetching" | "processing" | "publishing"
  | "published" | "no_results" | "duplicate" | "failed" | "rate_limited" | "unavailable" | "dead_letter";
export type JobRecord = { request_id: string; status: JobStatus; attempt?: number; [key: string]: unknown };
export interface JobEnv { CATALOG_JOBS_URL?: string; }

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

export async function loadJob(env: JobEnv, id: string): Promise<JobRecord | null> {
  if (!/^[a-f0-9]{64}$/.test(id)) throw new TypeError("invalid request_id");
  const prefix = env.CATALOG_JOBS_URL
    ?? "https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/data/jobs/";
  const url = prefix.replace(/\/?$/, "/") + id + ".json";
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("job storage unavailable");
  const data: unknown = await response.json();
  if (!data || typeof data !== "object") throw new Error("malformed job");
  const record = data as JobRecord;
  if (record.request_id !== id || typeof record.status !== "string" ||
      !new Set(["queued","searching","fetching","processing","publishing","published",
        "no_results","duplicate","failed","rate_limited","unavailable","dead_letter"]).has(record.status)) {
    throw new Error("invalid job");
  }
  return record;
}
