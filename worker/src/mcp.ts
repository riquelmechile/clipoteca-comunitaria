/** MCP 2026-07-28 + legacy 2025 Streamable HTTP, one server per request. */
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

const resultSchema = z.object({ result: z.unknown() });
const answer = (result: unknown) => ({
  content: [{ type: "text" as const, text: JSON.stringify(result) }],
  structuredContent: { result },
});
const fail = (message: string) => ({
  isError: true as const,
  content: [{ type: "text" as const, text: message }],
});
type ActionResult = ReturnType<typeof answer> | ReturnType<typeof fail>;

async function restResult(request: Request, env: McpEnv): Promise<ActionResult> {
  const response = await handleRest(request, env) ?? await handleCatalog(request, env);
  if (!response) return fail("Unknown route");
  if (!response.ok) return fail("Clipoteca API error " + response.status + ": " + await response.text());
  const payload: unknown = await response.json();
  return answer(payload);
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
    query: z.string().min(1).max(200).describe("Clip topic, searchable in Spanish or English"),
    aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
    max_duration: z.number().positive().max(20).optional(),
    min_width: z.number().int().positive().optional(),
    source: z.string().regex(/^[a-z0-9_]+$/).optional(),
    limit: z.number().int().min(1).max(100).optional(),
  };

  server.registerTool("search_clips", {
    title: "Search clips", description: "Search existing verified clips; optionally enqueue new footage.",
    inputSchema: z.strictObject({
      ...searchInput, import_if_missing: z.boolean().optional(),
    }),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, import_if_missing, ...filters }) => {
    return restResult(toolRequest("/v1/search", "POST", {
      query, filters: { ...filters, ...(import_if_missing ? { import_if_missing } : {}) },
    }), env);
  });

  server.registerTool("get_clip", {
    title: "Get clip", description: "Return verified catalogue metadata for one clip ID.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id, "GET"), env);
    if (!response?.ok) return fail("Clip not found: " + clip_id);
    return answer(await response.json());
  });

  server.registerTool("download_clip", {
    title: "Download clip URL",
    description: "Get a direct, real GitHub Releases MP4 link (never video bytes).",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id + "/download", "GET"), env);
    if (response?.status !== 302) return fail("Clip not found: " + clip_id);
    const url = response.headers.get("location");
    if (!url || !url.startsWith("https://")) return fail("No verified asset URL");
    return answer({ clip_id, url });
  });

  server.registerTool("search_and_import", {
    title: "Search and import", description: "Queue asynchronous footage search with write token.",
    inputSchema: z.strictObject({
      query: z.string().min(1).max(200),
      aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
      max_results: z.number().int().min(1).max(50).optional(),
    }),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, aspect_ratio, max_results }) => restResult(
    toolRequest("/v1/import", "POST", {
      query, filters: {
        ...(aspect_ratio ? { aspect_ratio } : {}),
        ...(max_results ? { limit: Math.min(max_results, 50) } : {}),
      },
    }), env,
  ));

  server.registerTool("get_job_status", {
    title: "Get job status", description: "Get queued/running/terminal state for a deterministic request ID.",
    inputSchema: z.strictObject({ request_id: z.string().regex(/^[a-f0-9]{64}$/) }),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ request_id }) =>
    restResult(toolRequest("/v1/jobs/" + request_id, "GET"), env));

  server.registerTool("list_sources", {
    title: "List public source statuses",
    description: "Show the 15 provider capability snapshot, not credentials or account-specific access.",
    inputSchema: z.strictObject({}),
    outputSchema: resultSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async () => {
    const response = await fetch(
      "https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/data/sources.json",
    );
    if (!response.ok) return fail("Source snapshot unavailable");
    const sources: unknown = await response.json();
    if (!Array.isArray(sources)) return fail("Malformed provider data");
    return answer({ sources, kind: "public_uncredentialed_snapshot" });
  });
  return server;
}

export async function handleMcp(request: Request, env: McpEnv): Promise<Response> {
  const hosts = (env.MCP_ALLOWED_HOSTS ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  const origins = (env.MCP_ALLOWED_ORIGINS ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!hosts.length) return Response.json({
    error: { code: "mcp_host_config_required", message: "MCP_ALLOWED_HOSTS must be configured" },
  }, { status: 503 });
  const rejection = hostHeaderValidationResponse(request, hosts) ??
    originValidationResponse(request, origins);
  if (rejection) return rejection;
  const handler = createMcpHandler(() => createClipotecaServer(env, request), {
    legacy: "stateless", maxRequestBodySize: 65536, responseMode: "json",
  });
  return handler.fetch(request);
}
