import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { expiredDeadLetter, isStaleJob, loadJob, requestId } from "../src/jobs";

const first = {
  id: "clip-000001", title: {es:"Lluvia",en:"Rain"},
  description: {es:"Lluvia",en:"Rain"}, tags:{es:["lluvia"],en:["rain"]},
  source:{provider:"nasa",source_language:"en"}, 
  media:{aspect_ratio:"9:16",duration_seconds:10,width:900,fps:30,has_audio:false},
  storage:{asset_url:"https://github.com/org/repo/releases/download/t/a.mp4"},
};

describe("M10 pagination, typed GET and stale jobs", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: {match: vi.fn(async () => undefined),put: vi.fn()} });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([
      first,
      {...first,id:"clip-000002"},
      {...first,id:"clip-000003"},
    ])));
  });

  const search = (filters: Record<string, unknown>) =>
    worker.fetch(new Request("https://api.test/v1/search", {
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({query:"lluvia",filters}),
    }));

  it("POST search honors cursor/limit and returns real next_cursor", async () => {
    const firstPage = await search({limit:1,cursor:"0"});
    expect(firstPage.status).toBe(200);
    expect(await firstPage.json()).toMatchObject({
      clips:[{id:"clip-000001"}],next_cursor:"1",
    });
    const second = await search({limit:1,cursor:"1"});
    expect(await second.json()).toMatchObject({
      clips:[{id:"clip-000002"}],next_cursor:"2",
    });
    const last = await search({limit:2,cursor:"2"});
    expect(await last.json()).toMatchObject({
      clips:[{id:"clip-000003"}],next_cursor:null,
    });
  });

  it("GET clips rejects malformed filters and typed 503 on backend failure", async () => {
    const bad = await worker.fetch(new Request("https://api.test/v1/clips?fps=nan"));
    expect(bad.status).toBe(400);
    expect(await bad.json()).toMatchObject({error:{code:"invalid_filter"}});
    const unknown = await worker.fetch(new Request("https://api.test/v1/clips?evil=1"));
    expect(unknown.status).toBe(400);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null,{status:500})));
    const failed = await worker.fetch(new Request("https://api.test/v1/clips?limit=2"));
    expect(failed.status).toBe(503);
    expect(await failed.json()).toMatchObject({error:{code:"catalog_unavailable"}});
  });

  it("request IDs change for every supported content filter", async () => {
    const base = {aspect_ratio:null,max_duration:20};
    const original = await requestId("lluvia",base);
    const filterCases = [
      {limit:2}, {cursor:"1"}, {fps:24}, {has_audio:true},
      {source:"pexels"}, {language:"es"}, {min_width:1080},
    ];
    for (const filter of filterCases) {
      expect(await requestId("lluvia",{...base,...filter})).not.toBe(original);
    }
  });

  it("jobs older than 45 minutes are stale; dead letters expire after 24h", () => {
    const old = new Date(Date.now()-46*60_000).toISOString();
    const newTime = new Date().toISOString();
    expect(isStaleJob({request_id:"a".repeat(64),status:"searching",updated_at:old})).toBe(true);
    expect(isStaleJob({request_id:"a".repeat(64),status:"searching",updated_at:newTime})).toBe(false);
    expect(isStaleJob({request_id:"a".repeat(64),status:"searching"})).toBe(true);
    expect(expiredDeadLetter({request_id:"a".repeat(64),status:"dead_letter",
      updated_at:new Date(Date.now()-25*3600_000).toISOString()})).toBe(true);
    expect(expiredDeadLetter({request_id:"a".repeat(64),status:"dead_letter",
      updated_at:newTime})).toBe(false);
  });

  it("loads fresh GitHub contents API base64 job records", async () => {
    const id = "f".repeat(64);
    const record = {request_id:id,status:"published",attempt:1};
    const content = btoa(JSON.stringify(record));
    const fetcher = vi.fn(async (url: RequestInfo | URL) => {
      expect(String(url instanceof Request?url.url:url)).toContain("/contents/data/jobs/");
      return Response.json({encoding:"base64",content});
    });
    vi.stubGlobal("fetch", fetcher);
    expect(await loadJob({}, id)).toMatchObject(record);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
