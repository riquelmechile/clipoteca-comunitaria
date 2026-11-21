import { beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../tests/fixtures/m22/semantic_vectors.json";
import {
  cosine, decodeFloat16, halfFloat, loadCorpus, normalizeEmbedding,
  normalizedQuery, queryVector, resetQueryDispatch, resetSemanticCache,
  rrfFuse, semanticSearch, sha256,
} from "../src/semantic";
import type { SemanticEnv } from "../src/semantic";
import worker from "../src/index";

const catalog = [
  {
    id: "clip-000101",
    title: {es:"Lluvia suave",en:"Rain on city"},
    description:{es:"Lluvia y agua",en:"Rainy street"},
    tags:{es:["lluvia"],en:["rain"]},
    source:{provider:"pexels",source_language:"en"},
    media:{aspect_ratio:"16:9",duration_seconds:15,width:1280,fps:30,has_audio:false},
    storage:{asset_url:"https://github.com/ex/x.mp4"},
  },
  {
    id:"clip-000102",
    title:{es:"Sol",en:"Sunshine"},
    description:{es:"D\u00eda soleado",en:"Sunny day"},
    tags:{es:["sol"],en:["sun"]},
    source:{provider:"pexels",source_language:"en"},
    media:{aspect_ratio:"16:9",duration_seconds:16,width:1280,fps:30,has_audio:false},
    storage:{asset_url:"https://github.com/ex/y.mp4"},
  },
];

const baseEnv: SemanticEnv = {
  CATALOG_URL:"https://api.github.test/catalog",
  CATALOG_TTL_SECONDS:"0",
  CLIPOTECA_GITHUB_REPO:"riquelmechile/clipoteca-comunitaria",
  CLIPOTECA_DISPATCH_TOKEN:"demo-token-123456789",
  CLIPOTECA_QUERY_DISPATCH_MODE:"1",
  SEMANTIC_SEARCH:"1",
};

function rawBytes(encoded: string): Uint8Array {
  return Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
}
function sourceResponse(raw: Uint8Array): Response {
  return new Response(Uint8Array.from(raw).buffer, {status:200,headers:{"content-type":"application/octet-stream"}});
}
function mockRemote(opts: {
  noManifest?: boolean;
  missingQueries?: boolean;
  malformedShard?: boolean;
  legacy?: boolean;
  encoder?: boolean;
} = {}) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    calls.push(url);
    if (url === baseEnv.CATALOG_URL) return Response.json(catalog);
    if (url.includes("/actions/workflows/ingest.yml/dispatches")) {
      return new Response(null,{status:204});
    }
    if (url === "https://vectors.example.com/encode" && opts.encoder) {
      return Response.json({vector:[1, ...new Array(511).fill(0)]});
    }
    if (url.endsWith("/queries/manifest.json")) return new Response(null,{status:404});
    if (url.endsWith("/manifest.json")) {
      return opts.noManifest
        ? new Response(null,{status:404})
        : Response.json(fixture.manifest);
    }
    if (/\/shards\/tail-[0-9a-f]{64}\.f16$/.test(url)) {
      const good = rawBytes(fixture.tail_base64);
      return sourceResponse(opts.malformedShard ? good.subarray(1) : good);
    }
    if (/\/ids\/tail-[0-9a-f]{64}\.json$/.test(url)) {
      return sourceResponse(rawBytes(fixture.tail_ids_base64));
    }
    if (url.includes("/shards/") || url.includes("/ids/")) {
      return new Response(null,{status:404});
    }
    if (url.endsWith("/index.json")) {
      return opts.legacy ? Response.json(fixture.legacy_index)
        : new Response(null,{status:404});
    }
    const legacyClip = Object.keys(fixture.legacy_vectors)
      .find(id => url.endsWith("/" + id + ".f16"));
    if (legacyClip && opts.legacy) {
      return sourceResponse(rawBytes(fixture.legacy_vectors[
        legacyClip as keyof typeof fixture.legacy_vectors
      ]));
    }
    const query = Object.values(fixture.queries).find(
      record => url.endsWith("/" + record.query_sha256 + ".json")
    );
    if (query && !opts.missingQueries) return Response.json(query);
    return new Response(null,{status:404});
  }));
  return calls;
}

beforeEach(() => {
  resetSemanticCache();
  resetQueryDispatch();
  vi.stubGlobal("caches", {default:{
    match:vi.fn(async () => undefined),
    put:vi.fn(async () => undefined),
  }});
});

describe("M22 binary Python fixture and rank fusion", () => {
  it("decodes little-endian float16 without Float16Array and renormalizes", () => {
    const buf = rawBytes(fixture.shard_base64);
    const first = decodeFloat16(buf,0);
    const second = decodeFloat16(buf,1024);
    expect(first).toHaveLength(512);
    expect(first[0]).toBeCloseTo(1,6);
    expect(first[1]).toBe(0);
    expect(second[1]).toBeCloseTo(1,6);
    expect(cosine(first,second)).toBeCloseTo(0,5);
    expect(halfFloat(0x3c00)).toBe(1);
    expect(halfFloat(0x0001)).toBeCloseTo(2**-24);
    expect(halfFloat(0xfc00)).toBe(-Infinity);
    expect(() => decodeFloat16(buf,1500)).toThrow();
    expect(() => normalizeEmbedding([1,2])).toThrow();
    expect(() => normalizeEmbedding(Array.from({length:512},(_,i)=>i===0?Infinity:0))).toThrow();
  });
  it("deterministically fuses reciprocal-rank scores including semantic-only IDs", () => {
    const fused = rrfFuse(["clip-000102"], [
      {id:"clip-000101", cosine:0.99},
      {id:"clip-000102", cosine:0.01},
    ]);
    expect(fused[0]?.id).toBe("clip-000102");
    expect(fused.map(x=>x.id)).toContain("clip-000101");
    expect(fused.every(x=>x.combined_score>0)).toBe(true);
  });
  it("matches query normalization and digest from Python", async () => {
    expect(normalizedQuery("  AGUACERO   ")).toBe("aguacero");
    expect(await sha256("aguacero")).toBe(fixture.queries.aguacero.query_sha256);
  });
});

describe("M22 Worker retrieval", () => {
  it.each(["aguacero", "lluvia", "rain", "\u4e0b\u96e8", "\u0434\u043e\u0436\u0434\u044c"])(
    "finds semantically related rain clip for %s using fake multilingual encoder",
    async term => {
      mockRemote();
      const page = await semanticSearch(
        new Request("https://clipoteca.test/v1/search",{method:"POST"}),
        baseEnv,term,new URLSearchParams({limit:"10"}),
      );
      expect(page.semantic).toBe("ready");
      expect(page.clips[0]?.id).toBe("clip-000101");
      expect(page.scores?.["clip-000101"]).toBeGreaterThan(0);
      expect(page.next_cursor).toBe(null);
    },
  );

  it("preserves old lexical results when semantic corpus unavailable", async () => {
    mockRemote({noManifest:true});
    const page = await semanticSearch(new Request("https://clipoteca.test/v1/search"),
      baseEnv,"lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.clips.map(c=>c.id)).toEqual(["clip-000101"]);
    expect(page.scores).toBeUndefined();
  });

  it("returns pending for uncached query and dispatches once", async () => {
    const calls = mockRemote({missingQueries:true});
    const request = new Request("https://clipoteca.test/v1/search",{method:"POST"});
    const first = await semanticSearch(request,baseEnv,"aguacero",new URLSearchParams());
    const second = await semanticSearch(request,baseEnv,"aguacero",new URLSearchParams());
    expect(first.semantic).toBe("pending");
    expect(first.clips).toHaveLength(0);
    expect(second.semantic).toBe("pending");
    expect(calls.filter(url=>url.endsWith("/dispatches"))).toHaveLength(1);
  });

  it("uses optional lightweight dispatch when YAML mode enabled", async () => {
    const calls = mockRemote({missingQueries:true});
    const page = await semanticSearch(new Request("https://clipoteca.test/v1/search"),
      {...baseEnv,CLIPOTECA_QUERY_DISPATCH_MODE:"1"},"aguacero",new URLSearchParams());
    expect(page.semantic).toBe("pending");
    expect(calls.some(url=>url.endsWith("/dispatches"))).toBe(true);
  });

  it("returns keyword-only unavailable when dispatch token missing", async () => {
    mockRemote({missingQueries:true});
    const page = await semanticSearch(new Request("https://clipoteca.test/v1/search"),
      {...baseEnv,CLIPOTECA_DISPATCH_TOKEN:""},"lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.clips.map(c=>c.id)).toEqual(["clip-000101"]);
  });

  it("accepts optional HTTPS self-hosted encoder and refuses private URLs", async () => {
    mockRemote({missingQueries:true,encoder:true});
    const good = await queryVector(
      {...baseEnv,QUERY_ENCODER_URL:"https://vectors.example.com/encode"},"aguacero"
    );
    expect(good?.length).toBe(512);
    expect(good?.[0]).toBeCloseTo(1);
    await expect(queryVector(
      {...baseEnv,QUERY_ENCODER_URL:"https://127.0.0.1/internal"},"aguacero"
    )).rejects.toThrow("unsafe_query_encoder_url");
  });

  it("supports limited M21 legacy index in transition", async () => {
    mockRemote({noManifest:true,legacy:true});
    const result = await loadCorpus(baseEnv);
    expect(result.get("clip-000101")?.[0]).toBeCloseTo(1);
    expect(result.get("clip-000102")?.[1]).toBeCloseTo(1);
  });

  it("verifies immutable shard digest before ranking", async () => {
    mockRemote({malformedShard:true});
    const page = await semanticSearch(new Request("https://clipoteca.test/v1/search"),
      baseEnv,"lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.clips[0]?.id).toBe("clip-000101");
  });

  it("caches manifest, shards and query vector across isolate searches", async () => {
    const calls = mockRemote();
    await loadCorpus(baseEnv);
    await loadCorpus(baseEnv);
    await queryVector(baseEnv,"aguacero");
    await queryVector(baseEnv,"aguacero");
    expect(calls.filter(x=>x.includes("/shards/"))).toHaveLength(1);
    expect(calls.filter(x=>x.includes("/ids/"))).toHaveLength(1);
    expect(calls.filter(x=>x.endsWith("/data/embeddings/manifest.json"))).toHaveLength(1);
    // One cached 404 for the r3 query manifest, plus one legacy JSON read.
    expect(calls.filter(x=>x.endsWith("/queries/manifest.json"))).toHaveLength(1);
    expect(calls.filter(x=>x.includes("/queries/"))).toHaveLength(2);
  });
});


describe("M22 REST entrypoint compatibility", () => {
  it("POST /v1/search includes semantic ready and scores without altering Clip", async () => {
    mockRemote();
    const response = await worker.fetch(new Request(
      "https://clipoteca.test/v1/search", {
        method:"POST",
        headers:{"content-type":"application/json"},
        body:JSON.stringify({query:"aguacero", filters:{limit:10}}),
      },
    ),baseEnv);
    expect(response.status).toBe(200);
    const body = await response.json() as {
      clips:typeof catalog; semantic:string; scores:Record<string,number>;
      status:string;
    };
    expect(body.semantic).toBe("ready");
    expect(body.clips[0]?.id).toBe("clip-000101");
    expect(body.scores["clip-000101"]).toBeGreaterThan(0);
    expect(body.status).toBe("published");
    expect(body.clips[0]).not.toHaveProperty("score");
  });
  it("POST /v1/search remains usable pending without cached text model", async () => {
    mockRemote({missingQueries:true});
    const response = await worker.fetch(new Request(
      "https://clipoteca.test/v1/search", {
        method:"POST",
        headers:{"content-type":"application/json"},
        body:JSON.stringify({query:"aguacero"}),
      },
    ),baseEnv);
    expect(response.status).toBe(200);
    const body=await response.json() as {semantic:string;clips:unknown[];scores?:unknown};
    expect(body.semantic).toBe("pending");
    expect(body.clips).toEqual([]);
    expect(body.scores).toBeUndefined();
  });
});


describe("M22 typed MCP search output", () => {
  it("returns ready and combined scores with validated clip ID keys", async () => {
    mockRemote();
    const request = new Request("https://clipoteca.test/mcp", {
      method:"POST",
      headers:{
        "content-type":"application/json",
        accept:"application/json, text/event-stream",
        host:"clipoteca.test",
        origin:"https://client.example",
      },
      body:JSON.stringify({
        jsonrpc:"2.0",method:"tools/call",id:1,
        params:{name:"search_clips",arguments:{query:"aguacero"}},
      }),
    });
    const response = await worker.fetch(request,{
      ...baseEnv,
      MCP_ALLOWED_HOSTS:"clipoteca.test",
      MCP_ALLOWED_ORIGINS:"https://client.example",
    });
    expect(response.status).toBe(200);
    const raw=await response.text();
    const payload=JSON.parse(raw.trimStart().startsWith("{")
      ? raw : raw.match(/(?:^|\n)data: (.+)/m)?.[1] ?? "{}") as {
        result?:{structuredContent?:{
          semantic:string; scores?:Record<string,number>;clips?:typeof catalog;
        }};
      };
    expect(payload.result?.structuredContent?.semantic).toBe("ready");
    expect(payload.result?.structuredContent?.clips?.[0]?.id).toBe("clip-000101");
    expect(payload.result?.structuredContent?.scores?.["clip-000101"]).toBeGreaterThan(0);
  });
});
