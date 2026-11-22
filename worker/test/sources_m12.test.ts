import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { sourceSnapshot } from "../src/mcp";

const environment = {
  MCP_ALLOWED_HOSTS: "clipoteca.test",
  MCP_ALLOWED_ORIGINS: "https://client.example",
};
const rows = [
  { name: "rutube", status: "BEST_EFFORT", capabilities: {
    search: "best_effort", preview: "supported", download: "best_effort",
  } },
  { name: "pexels", status: "SUPPORTED_SEARCH_DOWNLOAD", capabilities: {
    search: "supported", preview: "best_effort", download: "supported",
  } },
];
function mockSourceData(data: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(data)));
}
function rpc(method: string, params: unknown) {
  return new Request("https://clipoteca.test/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      host: "clipoteca.test",
      origin: "https://client.example",
    },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 }),
  });
}
async function payload(response: Response): Promise<any> {
  const body = await response.text();
  if (body.trimStart().startsWith("{")) return JSON.parse(body);
  const match = body.match(/(?:^|\n)data: (.+)/m);
  if (!match) throw new Error("Missing MCP JSON response");
  return JSON.parse(match[1]!);
}

describe("M12 REST/MCP source capability contract", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(), put: vi.fn() } });
    mockSourceData(rows);
  });

  it("GET /v1/sources exposes complete declared matrix without secret data", async () => {
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/sources"));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({
      kind: "actions_credential_presence_snapshot",
      sources: [
        { provider: "rutube", status: "BEST_EFFORT", capabilities: rows[0]!.capabilities },
        { provider: "pexels", status: "SUPPORTED_SEARCH_DOWNLOAD", capabilities: rows[1]!.capabilities },
      ],
    });
    const fetchArgs = vi.mocked(fetch).mock.calls[0]!;
    expect(String(fetchArgs[0])).toContain("/main/data/sources.json");
  });

  it("POST /v1/sources returns 405 and GET with source outage returns 503", async () => {
    const wrongMethod = await worker.fetch(new Request("https://clipoteca.test/v1/sources", {
      method: "POST",
    }));
    expect(wrongMethod.status).toBe(405);
    expect(wrongMethod.headers.get("Allow")).toBe("GET");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, {status: 503})));
    const down = await worker.fetch(new Request("https://clipoteca.test/v1/sources"));
    expect(down.status).toBe(503);
    expect(await down.json()).toMatchObject({error:{code:"source_unavailable"}});
  });

  it.each([
    [{name:"rutube", status:"ok"}],
    [{name:"rutube", status:"ok", capabilities:{search:"supported"}}],
    [{name:"rutube", status:"ok", capabilities:{
      search:"supported", preview:"fake", download:"unavailable",
    }}],
    [null],
  ])("fails closed on incomplete or invalid capabilities: %j", async (bad) => {
    mockSourceData(bad);
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/sources"));
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({error:{code:"source_unavailable"}});
  });

  it("MCP list_sources returns the same capabilities with valid output schema", async () => {
    const list = await worker.fetch(rpc("tools/list", {}), environment);
    const toolList = await payload(list);
    const tool = toolList.result.tools.find((item: {name: string}) => item.name === "list_sources");
    expect(tool.outputSchema.properties.sources).toBeDefined();

    const call = await worker.fetch(rpc("tools/call", {
      name: "list_sources", arguments: {},
    }), environment);
    expect(call.status).toBe(200);
    const body = await payload(call);
    expect(body.result.structuredContent).toEqual({
      kind: "actions_credential_presence_snapshot",
      sources: [
        { provider: "rutube", status: "BEST_EFFORT", capabilities: rows[0]!.capabilities },
        { provider: "pexels", status: "SUPPORTED_SEARCH_DOWNLOAD", capabilities: rows[1]!.capabilities },
      ],
    });
    expect(body.result.isError).not.toBe(true);
  });

  it("MCP list_sources reports a typed error rather than inventing unavailable", async () => {
    mockSourceData([{name:"rutube",status:"ok"}]);
    const call = await worker.fetch(rpc("tools/call", {
      name: "list_sources", arguments: {},
    }), environment);
    expect(call.status).toBe(200);
    const result = await payload(call);
    expect(result.result.isError).toBe(true);
    expect(JSON.stringify(result)).toContain("Source snapshot unavailable");
  });
});


it("M24 2.0.1 loads provider sources directly from raw GitHub", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    return Response.json(rows);
  }));
  const snapshot = await sourceSnapshot({});
  expect(snapshot.sources).toHaveLength(2);
  expect(calls).toEqual([
    "https://raw.githubusercontent.com/riquelmechile/clipoteca-comunitaria/main/data/sources.json",
  ]);
});
