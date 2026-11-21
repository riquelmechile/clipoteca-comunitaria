import { handleCatalog, type CatalogEnv } from "./catalog";
import { handleRest } from "./rest";

export interface Env extends CatalogEnv {
  CATALOG_TTL_SECONDS?: string;
  CLIPOTECA_DISPATCH_TOKEN?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CATALOG_JOBS_URL?: string;
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
}

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), {
  status,
  headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
});

export default {
  async fetch(request: Request, env: Env = {}): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/v1/health") {
      return json({ status: "ok", service: "clipoteca", version: "0.1.0" });
    }
    const restResponse = await handleRest(request, env);
    if (restResponse) return restResponse;
    const catalogResponse = await handleCatalog(request, env);
    if (catalogResponse) return catalogResponse;
    return json({ error: { code: "not_found", message: "Route not found" } }, 404);
  },
} satisfies ExportedHandler<Env>;

