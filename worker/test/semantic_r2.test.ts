import {beforeEach,describe,expect,it,vi} from "vitest";
import worker from "../src/index";
import fixture from "../../tests/fixtures/m22/semantic_vectors.json";
import normalization from "../../tests/fixtures/m22/normalization.json";
import {
  normalizedQuery, sha256, queryVector, queueQuery, semanticSearch,
  resetQueryDispatch, resetSemanticCache,
} from "../src/semantic";
import {fakeSecret} from "./fake-secrets";

const env = {
  CATALOG_URL:"https://example.test/catalog",
  CATALOG_TTL_SECONDS:"0",
  CLIPOTECA_GITHUB_REPO:"riquelmechile/clipoteca-comunitaria",
  CLIPOTECA_DISPATCH_TOKEN:fakeSecret("dispatch"),
  SEMANTIC_SEARCH:"1",
  CLIPOTECA_QUERY_DISPATCH_MODE:"1",
};
const rain={
  status: "published",
  id:"clip-000101",title:{es:"Lluvia",en:"Rain"},
  description:{es:"Tormenta",en:"Storm"},tags:{es:["lluvia"],en:["rain"]},
  source:{provider:"pexels",source_language:"es"},
  media:{aspect_ratio:"16:9",duration_seconds:10,width:1920,fps:30,has_audio:false},
  storage:{asset_url:"https://github.com/ex/a.mp4"},
};
const sun={...rain,id:"clip-000102",title:{es:"Sol",en:"Sunshine"},
  description:{es:"Sol",en:"Sun"},tags:{es:["sol"],en:["sun"]}};
const raw=(base64:string)=>Uint8Array.from(atob(base64),c=>c.charCodeAt(0));
const binary=(base64:string)=>new Response(Uint8Array.from(raw(base64)).buffer);
function persistentCache(){
  const data=new Map<string,Response>();
  const match=vi.fn(async (req:Request)=>data.get(req.url)?.clone());
  const put=vi.fn(async (req:Request,response:Response)=>{data.set(req.url,response.clone());});
  vi.stubGlobal("caches",{default:{match,put}});
  return {data,match,put};
}
function fakeRemote(opts:{
  missingQuery?:boolean;
  corruptSealed?:boolean;
  manifest?:unknown;
  oversize?:boolean;
  dispatchStatus?:number;
  encoder?:boolean;
}={}){
  const calls:{url:string;init?:RequestInit}[]=[];
  vi.stubGlobal("fetch",vi.fn(async (input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input instanceof Request?input.url:input);
    calls.push({url,...(init?{init}:{})});
    if(url===env.CATALOG_URL)return Response.json([rain,sun]);
    if(url.includes("/data/jobs/"))return new Response(null,{status:404});
    if(url.endsWith("/dispatches"))return new Response(null,{status:opts.dispatchStatus??204});
    if(url.endsWith("/queries/manifest.json"))return new Response(null,{status:404});
    if(url.endsWith("/manifest.json"))
      return opts.oversize?new Response(new Uint8Array(1_000_025).buffer):
      Response.json(opts.manifest??fixture.manifest);
    if(/\/shards\/tail-[0-9a-f]{64}\.f16$/.test(url))return binary(fixture.tail_base64);
    if(/\/ids\/tail-[0-9a-f]{64}\.json$/.test(url))return binary(fixture.tail_ids_base64);
    if(url.includes("/shards/shard-"))return new Response(null,{status:404});
    if(url.includes("/ids/shard-"))return new Response(null,{status:404});
    const cached=Object.values(fixture.queries).find(x=>url.endsWith("/"+x.query_sha256+".json"));
    if(url.includes("/queries/"))return cached&&!opts.missingQuery?Response.json(cached):
      new Response(null,{status:404});
    if(url==="https://encoder.example.com/encode"&&opts.encoder)
      return Response.json({vector:[1,...new Array(511).fill(0)]});
    return new Response(null,{status:404});
  }));
  return calls;
}
const request=(ip="192.0.2.1")=>new Request("https://clipoteca.test/v1/search",{
  headers:{"cf-connecting-ip":ip},
});
const sealed=(index:number)=>{
  const tail=fixture.manifest.tail!;
  const n=String(index).padStart(6,"0");
  return {...tail,count:256,
    path:"shards/shard-"+n+"-"+tail.sha256+".f16",
    ids_path:"ids/shard-"+n+"-"+tail.ids_sha256+".json"};
};

beforeEach(()=>{
  resetSemanticCache();resetQueryDispatch();
  persistentCache();
});

describe("M22 r2 safe dispatch controls",()=>{
  it("never fetches embeddings or dispatches when SEMANTIC_SEARCH is disabled",async()=>{
    const calls=fakeRemote();
    const page=await semanticSearch(request(),{...env,SEMANTIC_SEARCH:"0"},
      "lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.semantic_reason).toBe("semantic_disabled");
    expect(page.clips.map(x=>x.id)).toEqual(["clip-000101"]);
    expect(calls.some(c=>c.url.includes("/dispatches")||c.url.includes("/manifest.json"))).toBe(false);
  });
  it("mode off means unavailable and zero full-ingest dispatches",async()=>{
    const calls=fakeRemote({missingQuery:true});
    const page=await semanticSearch(request(),{...env,CLIPOTECA_QUERY_DISPATCH_MODE:"0"},
      "aguacero",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(calls.some(x=>x.url.endsWith("/dispatches"))).toBe(false);
  });
  it("dispatch only query_embedding and include normalized SHA request_id",async()=>{
    const calls=fakeRemote();
    const accepted=await queueQuery(request(),env,"  Aguacero  ");
    expect(accepted).toBe(true);
    const dispatch=calls.find(x=>x.url.endsWith("/dispatches"))!;
    const inputs=JSON.parse(String(dispatch.init?.body)).inputs as Record<string,string>;
    expect(inputs).toMatchObject({query:"aguacero",mode:"query_embedding",
      request_id:await sha256("aguacero")});
    expect(Object.keys(inputs).sort()).toEqual(["mode","query","request_id"]);
  });
  it("persists a 600-second failure marker even when GitHub returns 422",async()=>{
    const store=persistentCache();
    const calls=fakeRemote({dispatchStatus:422});
    expect(await queueQuery(request(),env,"probe")).toBe(false);
    resetQueryDispatch(); // simulate a new isolate, Cache API marker must remain
    expect(await queueQuery(request(),env,"probe")).toBe(false);
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(1);
    const marker=[...store.data].find(([key])=>key.includes("/request/"))?.[1];
    expect(marker?.headers.get("cache-control")).toContain("max-age=600");
    expect(await marker?.text()).toBe("failed");
  });
  it("per-IP quota prevents a second distinct query immediately",async()=>{
    const calls=fakeRemote();
    expect(await queueQuery(request("192.0.2.10"),env,"first")).toBe(true);
    expect(await queueQuery(request("192.0.2.10"),env,"second")).toBe(false);
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(1);
  });
  it("global cache-hour quota caps distinct clients at 20 attempts",async()=>{
    const calls=fakeRemote();
    let accepted=0;
    for(let i=0;i<21;i++){
      if(await queueQuery(request("192.0.2."+i),env,"probe-"+i))accepted++;
    }
    expect(accepted).toBe(20);
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(20);
  });
});

describe("M22 r2 corpus budget, isolation and relevance",()=>{
  it("matches Python normalization SHA for Greek, BOM, Cherokee, sharp S and multilingual",async()=>{
    for(const row of normalization){
      expect(normalizedQuery(row.input)).toBe(row.normalized);
      expect(await sha256(normalizedQuery(row.input))).toBe(row.sha256);
    }
  });
  it("accepts >32 manifest shards, but falls back before CPU work",async()=>{
    const manifest={...fixture.manifest,shards:Array.from({length:33},(_,i)=>sealed(i)),tail:null};
    const calls=fakeRemote({manifest});
    const page=await semanticSearch(request(),env,"lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.semantic_reason).toBe("cpu_budget_exceeded");
    expect(page.clips[0]?.id).toBe("clip-000101");
    expect(calls.some(x=>x.url.includes("/shards/shard-"))).toBe(false);
  });
  it("rejects large manifests with a typed fallback",async()=>{
    fakeRemote({oversize:true});
    const page=await semanticSearch(request(),env,"lluvia",new URLSearchParams());
    expect(page.semantic).toBe("unavailable");
    expect(page.semantic_reason).toBe("manifest_too_large");
    expect(page.clips[0]?.id).toBe("clip-000101");
  });
  it("a corrupt sealed shard is skipped without disabling valid tail",async()=>{
    const manifest={...fixture.manifest,shards:[sealed(0)]};
    fakeRemote({manifest});
    const page=await semanticSearch(request(),{...env,SEMANTIC_MAX_CLIPS:"512"},
      "aguacero",new URLSearchParams());
    expect(page.semantic).toBe("ready");
    expect(page.semantic_reason).toBe("shard_corrupt");
    expect(page.clips[0]?.id).toBe("clip-000101");
  });
  it("filters unrelated sunshine by cosine tau while retaining lexical rain",async()=>{
    const calls=fakeRemote();
    const page=await semanticSearch(request(),env,"aguacero",new URLSearchParams());
    expect(page.semantic).toBe("ready");
    expect(page.clips.map(x=>x.id)).toEqual(["clip-000101"]);
    const response=await worker.fetch(new Request("https://clipoteca.test/v1/search",{
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({query:"aguacero",filters:{import_if_missing:true}}),
    }),env);
    expect(response.status).toBe(200);
    const value=await response.json() as {clips:{id:string}[]};
    expect(value.clips.map(x=>x.id)).toEqual(["clip-000101"]);
    expect(calls.some(x=>x.url.endsWith("/dispatches"))).toBe(false);
  });
  it("import_if_missing with no relevant hits dispatches normal import, not query-only job",async()=>{
    const calls=fakeRemote({missingQuery:true});
    const response=await worker.fetch(new Request("https://clipoteca.test/v1/search",{
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({query:"unrelated",filters:{import_if_missing:true}}),
    }),env);
    expect(response.status).toBe(202);
    const obj=await response.json() as {status:string};
    expect(obj.status).toBe("queued");
    const dispatches=calls.filter(x=>x.url.endsWith("/dispatches"));
    expect(dispatches).toHaveLength(1);
    const inputs=JSON.parse(String(dispatches[0]?.init?.body)).inputs;
    expect(inputs.mode).toBeUndefined();
  });
});

describe("M22 r2 streamed optional encoder",()=>{
  it("rejects IPv6, private IPv4 and metadata endpoint even if configured by operator",async()=>{
    fakeRemote({missingQuery:true});
    for(const url of ["https://[::1]/encode","https://0.0.0.0/encode",
      "https://100.64.1.1/encode","https://127.0.0.1/encode"]){
      await expect(queryVector({...env,QUERY_ENCODER_URL:url},"aguacero",request()))
        .rejects.toThrow("unsafe_query_encoder_url");
    }
  });
  it("streams with strict body cap even without Content-Length, then caches failures",async()=>{
    const calls=fakeRemote({missingQuery:true});
    const original=globalThis.fetch;
    vi.stubGlobal("fetch",vi.fn(async (input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input instanceof Request?input.url:input);
      if(url==="https://encoder.example.com/encode")
        return new Response(new Uint8Array(20_000).buffer,{status:200});
      return original(input,init);
    }));
    expect(await queryVector({...env,QUERY_ENCODER_URL:"https://encoder.example.com/encode"},
      "aguacero",request())).toBeNull();
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(0);
  });
});
