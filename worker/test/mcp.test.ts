import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

const environment = { MCP_ALLOWED_HOSTS: "clipoteca.test", MCP_ALLOWED_ORIGINS: "https://client.example" };
const rpc = (method: string, params?: unknown, domain = "clipoteca.test", origin = "https://client.example") =>
  new Request("https://" + domain + "/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json, text/event-stream",
      ...(origin ? {origin} : {}), host: domain,
    },
    body: JSON.stringify({jsonrpc:"2.0", method, params, id:1}),
  });

async function rpcPayload(response: Response): Promise<any> {
  const raw = await response.text();
  if (raw.trimStart().startsWith("{")) return JSON.parse(raw);
  const line = raw.match(/(?:^|\n)data: (.+)/m);
  if (!line) throw new Error("Missing MCP SSE data: " + raw.slice(0, 120));
  return JSON.parse(line[1]!);
}

describe("M10 MCP Streamable HTTP", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
  });

  it("accepts its own workers.dev hostname by default without Origin", async () => {
    const request = rpc("tools/list", {}, "clipoteca.sub.workers.dev", "");
    const response = await worker.fetch(request);
    expect(response.status).toBe(200);
    const payload = (await rpcPayload(response)) as {result:{tools:Array<{name:string}>}};
    expect(payload.result.tools).toHaveLength(8);
    const invalid = rpc("tools/list", {}, "clipoteca.sub.workers.dev", "");
    invalid.headers.set("host", "malicious.test");
    expect((await worker.fetch(invalid)).status).toBe(403);
  });

  it("rejects untrusted Host and Origin with configured allowlists", async () => {
    const invalid = rpc("tools/list", {});
    invalid.headers.set("host", "malicious.test");
    expect((await worker.fetch(invalid, environment)).status).toBe(403);
    const foreign = rpc("tools/list", {});
    foreign.headers.set("origin", "https://attacker.example");
    expect((await worker.fetch(foreign, environment)).status).toBe(403);
  });

  it("registers eight official tools with real per-tool output schemas", async () => {
    const response = await worker.fetch(rpc("tools/list", {}), environment);
    expect(response.status).toBe(200);
    const payload = (await rpcPayload(response)) as {result:{tools:Array<{
      name:string; outputSchema:{type:string;properties:Record<string,unknown>}
    }>}};
    const tools = payload.result.tools;
    expect(tools.map(t=>t.name).sort()).toEqual([
      "download_clip", "get_clip", "get_job_status", "get_storyboard", "list_sources",
      "resolve_script", "search_and_import", "search_clips",
    ]);
    const properties:Record<string,string> = {
      search_clips:"clips",resolve_script:"scenes",get_clip:"clip",get_storyboard:"frames",download_clip:"url",
      search_and_import:"request_id",get_job_status:"request_id",list_sources:"sources",
    };
    for(const tool of tools) {
      expect(tool.outputSchema.type).toBe("object");
      expect(tool.outputSchema.properties).toHaveProperty(properties[tool.name]!);
      expect(tool.outputSchema.properties).not.toHaveProperty("result");
    }
  });

  it("returns unwrapped structuredContent for search and clip", async () => {
    const video = {
      id:"clip-000003", title:{es:"Lluvia",en:"Rain"},
      description:{es:"Lluvia",en:"Rain"}, tags:{es:["lluvia"],en:["rain"]},
      source:{provider:"pexels",source_language:"en"},
      media:{aspect_ratio:"9:16",duration_seconds:10,width:1080,fps:30,has_audio:false},
      storage:{asset_url:"https://github.com/example/release/file.mp4"},
    };
    vi.stubGlobal("fetch",vi.fn(async () => Response.json([video])));
    const call = rpc("tools/call",{name:"search_clips",arguments:{query:"lluvia"}});
    const response = await worker.fetch(call, environment);
    expect(response.status).toBe(200);
    const payload = (await rpcPayload(response)) as {result:{structuredContent:Record<string,unknown>}};
    expect(payload.result.structuredContent).toMatchObject({clips:[{id:"clip-000003"}],next_cursor:null});
    expect(payload.result.structuredContent).not.toHaveProperty("result");

    const clip = await worker.fetch(rpc("tools/call",{
      name:"get_clip",arguments:{clip_id:"clip-000003"},
    }),environment);
    const parsed = (await rpcPayload(clip)) as {result:{structuredContent:Record<string,unknown>}};
    expect(parsed.result.structuredContent).toMatchObject({clip:{id:"clip-000003"}});
  });

  it("blocks invalid tool arguments at SDK schema", async () => {
    const response = await worker.fetch(rpc("tools/call", {
      name: "get_clip", arguments: { clip_id: "../../secrets" },
    }), environment);
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("isError");
  });
});
