import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { handleCatalog } from "../src/catalog";

const clip = {
  id: "clip-000042",
  title: { es: "Río", en: "River" },
  description: { es: "Escena", en: "Scene" },
  tags: { es: ["río"], en: ["river"] },
  source: {
    provider: "internet_archive", provider_id: "video",
    source_language: "en", source_start: 22, source_end: 42,
  },
  media: { aspect_ratio: "16:9", duration_seconds: 20, width: 1280,
    fps: 25, has_audio: true },
  storage: {
    asset_url: "https://github.com/example/clip.mp4",
    thumbnail_url: "https://github.com/example/thumb.webp",
    storyboard_urls: Array.from({length: 6}, (_,i) =>
      "https://github.com/example/story-" + i + ".webp"),
  },
};

describe("M14 storyboard metadata is public and bounded", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", {default:{match: vi.fn(async()=>undefined), put: vi.fn()}});
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([clip])));
  });

  it("GET /v1/clips/id/storyboard lists six WebP frames in order", async () => {
    const response = await handleCatalog(
      new Request("https://clipoteca.test/v1/clips/clip-000042/storyboard"), {},
    );
    expect(response?.status).toBe(200);
    const body = await response?.json() as {clip_id:string;frames:Array<{
      url:string;index:number;timestamp_seconds:number}>};
    expect(body.clip_id).toBe("clip-000042");
    expect(body.frames).toHaveLength(6);
    expect(body.frames[0]).toMatchObject({
      index:0, timestamp_seconds:20/12,
      url:"https://github.com/example/story-0.webp",
    });
    const original = await handleCatalog(
      new Request("https://clipoteca.test/v1/clips/clip-000042"), {},
    );
    expect((await original?.json() as typeof clip).source.source_start).toBe(22);
  });

  it("MCP get_storyboard returns structured content, never MP4 bytes", async () => {
    const request = new Request("https://clipoteca.test/mcp", {
      method:"POST",
      headers: {host:"clipoteca.test",origin:"https://client.example",
        "content-type":"application/json",accept:"application/json, text/event-stream"},
      body: JSON.stringify({jsonrpc:"2.0",id:1,method:"tools/call",
        params:{name:"get_storyboard",arguments:{clip_id:"clip-000042"}}}),
    });
    const response = await worker.fetch(request, {
      MCP_ALLOWED_HOSTS:"clipoteca.test", MCP_ALLOWED_ORIGINS:"https://client.example",
    });
    expect(response.status).toBe(200);
    const raw = await response.text();
    const data = raw.trimStart().startsWith("{")
      ? JSON.parse(raw) : JSON.parse(raw.match(/(?:^|\n)data: (.+)/m)![1]!);
    expect(data.result.structuredContent.frames).toHaveLength(6);
    expect(raw).not.toContain("clip.mp4");
  });

  it("legacy clips without preview links give an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([
      {...clip, storage:{asset_url:clip.storage.asset_url}},
    ])));
    const response = await handleCatalog(
      new Request("https://clipoteca.test/v1/clips/clip-000042/storyboard"), {},
    );
    expect(await response?.json()).toEqual({clip_id:"clip-000042",frames:[]});
  });
});
