/** MCP 2026-07-28 and legacy 2025 Streamable HTTP, one server per request. */
import {
  createMcpHandler, McpServer, hostHeaderValidationResponse, originValidationResponse,
} from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import { handleCatalog } from "./catalog";
import { handleRest, type RestEnv } from "./rest";

export interface McpEnv extends RestEnv {
  MCP_ALLOWED_HOSTS?: string;
  MCP_ALLOWED_ORIGINS?: string;
}

const clipSchema = z.object({
  id: z.string().regex(/^clip-\d{6}$/),
  title: z.object({ es: z.string(), en: z.string() }),
  description: z.object({ es: z.string(), en: z.string() }),
  tags: z.object({ es: z.array(z.string()), en: z.array(z.string()) }),
  source: z.object({
    provider: z.string(), source_language: z.string(),
    provider_id: z.string().optional(), source_title: z.string().optional(),
    source_url: z.string().optional(), source_start: z.number().nonnegative().optional(),
    source_end: z.number().positive().optional(), issue_number: z.number().int().optional(),
    issue_author: z.string().optional(), issue_url: z.string().optional(),
  }),
  media: z.object({
    aspect_ratio: z.string(), duration_seconds: z.number(), width: z.number(),
    fps: z.number(), has_audio: z.boolean(),
  }),
  storage: z.object({
    asset_url: z.string(), thumbnail_url: z.string().optional(),
    storyboard_urls: z.array(z.string().url()).max(8).optional(),
  }),
  status: z.string().optional(), created_at: z.string().optional(),
});
const searchOutputSchema = z.object({
  clips: z.array(clipSchema), next_cursor: z.string().nullable(),
  status: z.string().optional(), request_id: z.string().optional(),
  semantic: z.enum(["ready", "pending", "unavailable"]).optional(),
  scores: z.record(z.string().regex(/^clip-\d{6}$/), z.number().finite()).optional(),
});
const getClipOutputSchema = z.object({ clip: clipSchema });
const downloadOutputSchema = z.object({ url: z.string().url(), clip_id: z.string().optional() });
const storyboardOutputSchema = z.object({
  clip_id: z.string(),
  frames: z.array(z.object({
    url: z.string().url(), index: z.number().int().nonnegative(),
    timestamp_seconds: z.number().nonnegative(),
  })).max(8),
});
const importOutputSchema = z.object({ request_id: z.string(), status: z.string() });
const jobOutputSchema = z.object({
  request_id: z.string(), status: z.string(), attempt: z.number().int().optional(),
  updated_at: z.string().optional(),
  language_counts: z.record(z.string(), z.number()).optional(),
  provider_counts: z.record(z.string(), z.number()).optional(),
  source_errors: z.record(z.string(), z.string()).optional(),
  terms: z.record(z.string(), z.string()).optional(),
  duplicate_ids: z.array(z.string()).optional(),
});
const capabilityLevel = z.enum(["supported", "best_effort", "unavailable"]);
const sourceCapabilities = z.object({ search: capabilityLevel, preview: capabilityLevel, download: capabilityLevel });
const sourcesOutputSchema = z.object({
  sources: z.array(z.object({ provider: z.string(), status: z.string(), capabilities: sourceCapabilities })),
  kind: z.string().optional(),
});
const answer = (result: object) => ({
  content: [{ type: "text" as const, text: JSON.stringify(result) }],
  structuredContent: result,
});
const fail = (message: string) => ({
  isError: true as const,
  content: [{ type: "text" as const, text: message }],
});
type ActionResult = ReturnType<typeof answer> | ReturnType<typeof fail>;

async function restResult(request: Request, env: McpEnv): Promise<ActionResult> {
  const response = await handleRest(request, env) ?? await handleCatalog(request, env);
  if (!response) return fail("Unknown route");
  if (!response.ok) return fail("Clipoteca API error " + response.status);
  const result = (await response.json()) as object;
  return answer(result);
}
const base64Json = (data: string): object => {
  const binary = atob(data.replace(/\s+/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as object;
};

export async function sourceSnapshot(env: McpEnv): Promise<{ sources: {provider: string; status: string; capabilities: {search: string; preview: string; download: string}}[];kind: string }> {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error("invalid repository");
  const response = await fetch(
    "https://api.github.com/repos/" + repo + "/contents/data/sources.json?ref=main",
    { headers: { accept: "application/vnd.github+json",
      "cache-control": "no-cache", "user-agent": "clipoteca-worker" } },
  );
  if (!response.ok) throw new Error("source snapshot unavailable");
  const raw: unknown = await response.json();
  let sources: unknown = raw;
  if (raw && typeof raw === "object" && "encoding" in raw &&
      (raw as {encoding?: unknown}).encoding === "base64") {
    const data = (raw as {content?: unknown}).content;
    if (typeof data !== "string") throw new Error("missing source content");
    sources = base64Json(data);
  }
  if (!Array.isArray(sources)) throw new Error("malformed provider data");
  const rows = sources.map((rawRow: unknown) => {
    if (!rawRow || typeof rawRow !== "object" || Array.isArray(rawRow)) {
      throw new Error("invalid provider row");
    }
    const row = rawRow as Record<string, unknown>;
    const provider = row.name ?? row.provider;
    const status = row.status;
    // Missing capabilities is invalid; do not silently invent "unavailable".
    const parsed = sourceCapabilities.safeParse(row.capabilities);
    if (typeof provider !== "string" || !provider ||
        typeof status !== "string" || !status || !parsed.success) {
      throw new Error("invalid provider row or capabilities");
    }
    return { provider, status, capabilities: parsed.data };
  });
  return { sources: rows, kind: "actions_credential_presence_snapshot" };
}

export function createClipotecaServer(env: McpEnv, inbound: Request): McpServer {
  const server = new McpServer(
    { name: "clipoteca-comunitaria", version: "1.0.0" },
    { capabilities: { tools: {} } },
  );

  const toolRequest = (path: string, method: string, body?: unknown): Request => {
    const headers = new Headers({ accept: "application/json" });
    const ip = inbound.headers.get("cf-connecting-ip");
    if (ip) headers.set("cf-connecting-ip", ip);
    const authorization = inbound.headers.get("authorization");
    if (authorization) headers.set("authorization", authorization);
    if (body !== undefined) headers.set("content-type", "application/json");
    return new Request("https://clipoteca.internal" + path, {
      method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  };
  const clipId = z.string().regex(/^clip-\d{6}$/);
  const searchInput = {
    query: z.string().min(1).max(200),
    aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
    max_duration: z.number().positive().max(20).optional(),
    min_width: z.number().int().positive().optional(),
    fps: z.number().positive().optional(),
    source: z.string().regex(/^[a-z0-9_]+$/).optional(),
    language: z.enum(["es", "en", "zh", "ru"]).optional(),
    has_audio: z.boolean().optional(),
    limit: z.number().int().min(1).max(100).optional(),
    cursor: z.string().regex(/^(0|[1-9][0-9]*)$/).optional(),
  };

  server.registerTool("search_clips", {
    title: "Search clips", description: "Search verified clips; optionally queue an import.",
    inputSchema: z.strictObject({
      ...searchInput, import_if_missing: z.boolean().optional(),
    }),
    outputSchema: searchOutputSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, import_if_missing, ...filters }) => restResult(
    toolRequest("/v1/search", "POST", {
      query, filters: { ...filters, ...(import_if_missing ? { import_if_missing } : {}) },
    }), env));

  server.registerTool("get_clip", {
    title: "Get clip", description: "Return catalogue metadata for one verified clip.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: getClipOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id, "GET"), env);
    if (!response?.ok) return fail("Clip unavailable: " + clip_id);
    return answer({ clip: await response.json() });
  });

  server.registerTool("get_storyboard", {
    title: "Inspect video storyboard",
    description: "Return immutable WebP preview frame URLs and timestamps before downloading video.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: storyboardOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(
      toolRequest("/v1/clips/" + clip_id + "/storyboard", "GET"), env,
    );
    if (!response?.ok) return fail("Clip unavailable: " + clip_id);
    return answer(await response.json() as object);
  });

  server.registerTool("download_clip", {
    title: "Download clip URL", description: "Return verified GitHub Releases MP4 URL, not bytes.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: downloadOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id + "/download", "GET"), env);
    if (response?.status !== 302) return fail("Clip unavailable: " + clip_id);
    const url = response.headers.get("location");
    if (!url || !url.startsWith("https://")) return fail("No verified asset URL");
    return answer({ clip_id, url });
  });

  server.registerTool("search_and_import", {
    title: "Search and import", description: "Queue asynchronous import; requires write token.",
    inputSchema: z.strictObject({
      query: z.string().min(1).max(200),
      aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
      max_results: z.number().int().min(1).max(50).optional(),
    }),
    outputSchema: importOutputSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, aspect_ratio, max_results }) => restResult(
    toolRequest("/v1/import", "POST", {
      query, filters: {
        ...(aspect_ratio ? { aspect_ratio } : {}),
        ...(max_results ? { limit: max_results } : {}),
      },
    }), env));

  server.registerTool("get_job_status", {
    title: "Get job status", description: "Fetch durable job record or a typed not_found.",
    inputSchema: z.strictObject({ request_id: z.string().regex(/^[a-f0-9]{64}$/) }),
    outputSchema: jobOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ request_id }) => restResult(
    toolRequest("/v1/jobs/" + request_id, "GET"), env));

  server.registerTool("list_sources", {
    title: "List public source statuses",
    description: "Read credential-presence status snapshot without exposing any values.",
    inputSchema: z.strictObject({}),
    outputSchema: sourcesOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async () => {
    try { return answer(await sourceSnapshot(env)); }
    catch { return fail("Source snapshot unavailable"); }
  });
  return server;
}

export async function handleMcp(request: Request, env: McpEnv): Promise<Response> {
  const ownHost = new URL(request.url).hostname.toLowerCase();
  const explicitlyAllowed = (env.MCP_ALLOWED_HOSTS ?? "").split(",")
    .map(x => x.trim().toLowerCase()).filter(Boolean);
  const hosts = explicitlyAllowed.length ? explicitlyAllowed : [ownHost];
  const origins = (env.MCP_ALLOWED_ORIGINS ?? "").split(",").map(x => {
    const entry = x.trim();
    if (!entry) return "";
    try { return new URL(entry).hostname.toLowerCase(); }
    catch { return entry.toLowerCase(); }
  }).filter(Boolean);
  // Reject a forged Host even when the allowlist falls back to the Worker URL.
  const suppliedHost = request.headers.get("host")?.toLowerCase().split(":")[0];
  if (!explicitlyAllowed.length && suppliedHost && suppliedHost !== ownHost) {
    return Response.json({ error: {code:"invalid_host"} }, {status:403});
  }
  const rejection = hostHeaderValidationResponse(request, hosts) ??
    originValidationResponse(request, origins);
  if (rejection) return rejection;
  const handler = createMcpHandler(() => createClipotecaServer(env, request), {
    legacy: "stateless", maxRequestBodySize: 65536, responseMode: "json",
  });
  return handler.fetch(request);
}
