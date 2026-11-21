import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

const environment = { MCP_ALLOWED_HOSTS: "clipoteca.test", MCP_ALLOWED_ORIGINS: "https://client.example" };
const rpc = (method: string, params?: unknown) => new Request("https://clipoteca.test/mcp", {
  method: "POST",
  headers: {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    origin: "https://client.example",
    host: "clipoteca.test",
  },
  body: JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 }),
});

describe("M7b Streamable HTTP MCP", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  });

  it("rejects absent allowlist and foreign Host or Origin", async () => {
    expect((await worker.fetch(rpc("tools/list", {}))).status).toBe(503);
    const invalid = rpc("tools/list", {});
    invalid.headers.set("host", "malicious.test");
    expect((await worker.fetch(invalid, environment)).status).toBe(403);
    const fromForeign = rpc("tools/list", {});
    fromForeign.headers.set("origin", "https://attacker.example");
    expect((await worker.fetch(fromForeign, environment)).status).toBe(403);
  });

  it("registers exactly six official tools without embedding media", async () => {
    const response = await worker.fetch(rpc("tools/list", {}), environment);
    expect(response.status).toBe(200);
    const text = await response.text();
    for (const name of [
      "search_clips", "get_clip", "download_clip",
      "search_and_import", "get_job_status", "list_sources",
    ]) expect(text).toContain(name);
    expect(text).not.toContain("base64_video");
  });

  it("blocks invalid tool arguments at SDK schema", async () => {
    const response = await worker.fetch(rpc("tools/call", {
      name: "get_clip", arguments: { clip_id: "../../secrets" },
    }), environment);
    expect(response.status).toBe(200);
    const text = await response.text();
    expect(text).toContain("isError");
  });
});
