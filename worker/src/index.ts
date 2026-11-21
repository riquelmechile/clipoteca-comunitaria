import { handleCatalog, loadCatalog, type CatalogEnv } from "./catalog";
import { handleRest } from "./rest";
import { createClipotecaServer, handleMcp, sourceSnapshot } from "./mcp";

export interface Env extends CatalogEnv {
  CATALOG_TTL_SECONDS?: string;
  CLIPOTECA_DISPATCH_TOKEN?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CATALOG_JOBS_URL?: string;
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
  MCP_ALLOWED_HOSTS?: string;
  MCP_ALLOWED_ORIGINS?: string;
  QUERY_ENCODER_URL?: string;
  CLIPOTECA_QUERY_DISPATCH_MODE?: string;
  SEMANTIC_SEARCH?: string;
  SEMANTIC_MAX_CLIPS?: string;
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

async function health(request: Request, env: Env): Promise<Response> {
  let catalogue = "ok";
  let items = 0;
  let mcp = "ok";
  try { items = (await loadCatalog(env)).length; }
  catch { catalogue = "unavailable"; }
  try { createClipotecaServer(env, request); }
  catch { mcp = "unavailable"; }
  const healthy = catalogue === "ok" && mcp === "ok";
  return json({
    status: healthy ? "ok" : "degraded",
    service: "clipoteca",
    version: "1.0.0",
    checks: {catalogue, mcp, items},
    ...(!healthy ? {error: {code: "health_unavailable"}} : {}),
  }, healthy ? 200 : 503);
}

const METHOD_PATHS = [
  /^\/v1\/health$/,
  /^\/v1\/sources$/,
  /^\/v1\/clips(?:\/clip-\d{6}(?:\/download)?)?$/,
  /^\/v1\/jobs\/[a-z0-9]+$/,
  /^\/v1\/(?:search|import|contributions)$/,
];
const knownMethod = (path: string) =>
  ["/v1/search", "/v1/import", "/v1/contributions"].includes(path) ? "POST" : "GET";

export default {
  async fetch(request: Request, env: Env = {}): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/mcp") return handleMcp(request, env);
    if (url.pathname === "/v1/sources" && request.method === "GET") {
      try {
        return json(await sourceSnapshot(env));
      } catch {
        return json({
          error: { code: "source_unavailable", message: "Source snapshot unavailable" },
        }, 503);
      }
    }
    if (url.pathname === "/v1/health" && request.method === "GET") {
      return health(request, env);
    }
    if (METHOD_PATHS.some(r => r.test(url.pathname)) &&
        request.method !== knownMethod(url.pathname)) {
      const allowed = knownMethod(url.pathname);
      const response = json({error:{code:"method_not_allowed", message:"Method not allowed"}},405);
      response.headers.set("Allow", allowed);
      return response;
    }
    const restResponse = await handleRest(request, env);
    if (restResponse) return restResponse;
    const catalogResponse = await handleCatalog(request, env);
    if (catalogResponse) return catalogResponse;
    return json({ error: { code: "not_found", message: "Route not found" } }, 404);
  },
} satisfies ExportedHandler<Env>;
