import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

describe("health route", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  });

  it("checks real catalogue access and mounted MCP", async () => {
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/health"));
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      status: "ok", service: "clipoteca", version: "2.0.5",
      checks: { catalogue: "ok", mcp: "ok", items: 0 },
    });
  });

  it("returns degraded 503 when catalog storage is inaccessible", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, {status:503})));
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/health"));
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      status: "degraded", checks: {catalogue: "unavailable"},
      error: {code:"health_unavailable"},
    });
  });

  it("returns 405 for unsupported known route methods and typed 404 otherwise", async () => {
    const wrong = await worker.fetch(new Request("https://clipoteca.test/v1/health", {method:"POST"}));
    expect(wrong.status).toBe(405);
    expect(wrong.headers.get("Allow")).toBe("GET");
    const missing = await worker.fetch(new Request("https://clipoteca.test/nope"));
    expect(missing.status).toBe(404);
  });
});
