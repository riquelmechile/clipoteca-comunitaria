import {beforeEach, describe, expect, it, vi} from "vitest";
import normalization from "../../tests/fixtures/m22/normalization_r3.json";
import fixture from "../../tests/fixtures/m22/semantic_vectors.json";
import {
  normalizedQuery, sha256, queueQuery, resetQueryDispatch, resetSemanticCache,
  selectSemanticMatches, semanticThreshold, queryVector, semanticSearch,
} from "../src/semantic";
import {resetQuotaReservations} from "../src/quota";

const env={
  CATALOG_URL:"https://example.test/catalog",
  CLIPOTECA_GITHUB_REPO:"riquelmechile/clipoteca-comunitaria",
  CLIPOTECA_DISPATCH_TOKEN:"test-token-do-not-share",
  CLIPOTECA_QUERY_DISPATCH_MODE:"1",
  SEMANTIC_SEARCH:"1",
};
type Entry={url:string; init?:RequestInit};
function delayedCache(ms=3){
  const data=new Map<string,Response>();
  vi.stubGlobal("caches",{default:{
    match:vi.fn(async (req:Request)=>{
      await new Promise(resolve=>setTimeout(resolve,ms));
      return data.get(req.url)?.clone();
    }),
    put:vi.fn(async (req:Request,value:Response)=>{
      await new Promise(resolve=>setTimeout(resolve,ms));
      data.set(req.url,value.clone());
    }),
  }});
  return data;
}
function fakeDispatch(){
  const calls:Entry[]=[];
  vi.stubGlobal("fetch",vi.fn(async (input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input instanceof Request?input.url:input);
    calls.push({url,...(init?{init}:{})});
    if(url.endsWith("/dispatches"))return new Response(null,{status:204});
    return new Response(null,{status:404});
  }));
  return calls;
}
const request=(ip:string)=>new Request("https://clipoteca.test/v1/search",{
  headers:{"cf-connecting-ip":ip},
});
beforeEach(()=>{
  resetSemanticCache();resetQueryDispatch();resetQuotaReservations();
  delayedCache(2);
});

describe("M22 r3 semantic threshold",()=>{
  it("defaults to 0.20 with configurable absolute minimum",()=>{
    expect(semanticThreshold(env)).toBe(0.20);
    expect(semanticThreshold({...env,SEMANTIC_MIN_COSINE:"0.19"})).toBe(0.19);
    expect(semanticThreshold({...env,SEMANTIC_MIN_COSINE:"bogus"})).toBe(0.20);
  });
  it("includes measured real CLIP 0.22\u20130.28 and rejects unrelated 0.17",()=>{
    const selected=selectSemanticMatches([
      {id:"clip-000001",cosine:0.27},
      {id:"clip-000002",cosine:0.24},
      {id:"clip-000003",cosine:0.22},
      {id:"clip-000004",cosine:0.17},
    ]);
    expect(selected.map(row=>row.id)).toEqual([
      "clip-000001","clip-000002","clip-000003",
    ]);
    expect(selectSemanticMatches([{id:"clip-000100",cosine:0.22}])).toHaveLength(1);
    expect(selectSemanticMatches([{id:"clip-000100",cosine:0.17}])).toEqual([]);
    const hundred=Array.from({length:100},(_,i)=>({
      id:"clip-"+String(i).padStart(6,"0"),cosine:0.28,
    }));
    expect(selectSemanticMatches(hundred)).toHaveLength(50);
  });
});

describe("M22 r3 concurrent cost-control reservations",()=>{
  it("50 simultaneous same-IP distinct queries dispatch at most one",async()=>{
    const calls=fakeDispatch();
    const promises=Array.from({length:50},(_,i)=>
      queueQuery(request("192.0.2.10"),env,"query-"+i));
    const accepted=(await Promise.all(promises)).filter(Boolean);
    expect(accepted).toHaveLength(1);
    expect(calls.filter(row=>row.url.endsWith("/dispatches"))).toHaveLength(1);
  });
  it("50 simultaneous different-IP requests for same SHA dispatch only one",async()=>{
    const calls=fakeDispatch();
    const promises=Array.from({length:50},(_,i)=>
      queueQuery(request("198.51.100."+i),env,"  Aguacero  "));
    const accepted=(await Promise.all(promises)).filter(Boolean);
    expect(accepted).toHaveLength(1);
    expect(calls.filter(row=>row.url.endsWith("/dispatches"))).toHaveLength(1);
  });
  it("50 simultaneous distinct-IP/distinct-digest never exceed 20/h",async()=>{
    const calls=fakeDispatch();
    const promises=Array.from({length:50},(_,i)=>
      queueQuery(request("203.0.113."+i),env,"different-"+i));
    const accepted=(await Promise.all(promises)).filter(Boolean);
    expect(accepted).toHaveLength(20);
    expect(calls.filter(row=>row.url.endsWith("/dispatches"))).toHaveLength(20);
  });
  it("quota-IP rejection does not poison the digest for another IP",async()=>{
    const calls=fakeDispatch();
    expect(await queueQuery(request("192.0.2.10"),env,"first")).toBe(true);
    expect(await queueQuery(request("192.0.2.10"),env,"other")).toBe(false);
    expect(await queueQuery(request("192.0.2.11"),env,"other")).toBe(true);
    expect(calls.filter(row=>row.url.endsWith("/dispatches"))).toHaveLength(2);
  });
  it("global-cap rejection does not poison SHA for later window/client",async()=>{
    const calls=fakeDispatch();
    for(let i=0;i<20;i++)expect(
      await queueQuery(request("192.0.2."+i),env,"q-"+i)
    ).toBe(true);
    expect(await queueQuery(request("192.0.2.25"),env,"blocked")).toBe(false);
    const cache=(caches as CacheStorage & {default:Cache}).default;
    const deniedKey=new Request("https://clipoteca.invalid/_query-embed/request/"+
      await sha256("blocked"));
    expect(await cache.match(deniedKey)).toBeUndefined();
    expect(calls.filter(row=>row.url.endsWith("/dispatches"))).toHaveLength(20);
  });
});

describe("M22 r3 Unicode and query shards",()=>{
  it("matches Python Unicode 17.0.0 casefold+digest across all cases",async()=>{
    expect(normalization.unicode).toBe("17.0.0");
    for(const row of normalization.cases){
      expect(normalizedQuery(row.input)).toBe(row.normalized);
      expect(await sha256(normalizedQuery(row.input))).toBe(row.sha256);
    }
  });
  it("loads bounded query shard vector and ignores legacy JSON path",async()=>{
    const digest=await sha256("aguacero");
    const idsRaw=new TextEncoder().encode(JSON.stringify([digest])+"\n");
    const vector=Uint8Array.from(atob(fixture.queries.aguacero.vector),
      char=>char.charCodeAt(0));
    const idsSha=await sha256(idsRaw),vectorSha=await sha256(vector);
    const entry={path:"shards/tail-"+vectorSha+".f16",
      ids_path:"ids/tail-"+idsSha+".json",count:1,
      sha256:vectorSha,ids_sha256:idsSha};
    const manifest={version:1,dimension:512,dtype:"float16-le",
      seal_capacity:64,max_queries:512,shards:[],tail:entry};
    const calls:string[]=[];
    vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input instanceof Request?input.url:input);
      calls.push(url);
      if(url.endsWith("/queries/manifest.json"))return Response.json(manifest);
      if(url.endsWith("/queries/"+entry.ids_path))
        return new Response(Uint8Array.from(idsRaw).buffer);
      if(url.endsWith("/queries/"+entry.path))
        return new Response(Uint8Array.from(vector).buffer);
      return new Response(null,{status:404});
    }));
    const loaded=await queryVector(env,"aguacero");
    expect(loaded?.[0]).toBeGreaterThan(0.99);
    expect(calls.some(x=>x.endsWith("/queries/"+digest+".json"))).toBe(false);
  });
});

describe("M22 r3 hardened encoder allowlist",()=>{
  it("blocks dotted localhost and DNS rebinding helper domains",async()=>{
    fakeDispatch();
    for(const host of ["localhost.","x.internal.","x.lan","foo.corp.",
      "home.home.arpa","foo.127.0.0.1.nip.io","bar.sslip.io"]){
      await expect(queryVector({...env,QUERY_ENCODER_URL:"https://"+host+"/encode"},
        "probe",request("203.0.113.20"))).rejects.toThrow("unsafe_query_encoder_url");
    }
  });
  it("optionally requires a configured hostname allowlist",async()=>{
    fakeDispatch();
    await expect(queryVector({
      ...env,QUERY_ENCODER_URL:"https://encoder.example.com/encode",
      QUERY_ENCODER_HOSTS:"allowed.example.com",
    },"probe",request("203.0.113.20"))).rejects.toThrow("unsafe_query_encoder_url");
  });
});


describe("M22 r3 no-match response contract",()=>{
  it("does not report semantic ready with zero candidates above threshold",async()=>{
    const query="mystery",digest=await sha256(query);
    const orthogonal=new Uint8Array(1024);
    orthogonal[4]=0;orthogonal[5]=0x3c; // 1.0 in dimension 2, orthogonal to clip dimensions 0 and 1
    const encoded=btoa(String.fromCharCode(...orthogonal));
    const tail=fixture.manifest.tail!;
    const sample={
      status: "published",
      id:"clip-000101",title:{es:"Lluvia",en:"Rain"},
      description:{es:"Agua",en:"Water"},tags:{es:["lluvia"],en:["rain"]},
      source:{provider:"pexels",source_language:"en"},
      media:{aspect_ratio:"16:9",duration_seconds:10,width:1920,fps:30,has_audio:false},
    };
    vi.stubGlobal("fetch",vi.fn(async (input:RequestInfo|URL)=>{
      const url=String(input instanceof Request?input.url:input);
      if(url===env.CATALOG_URL)return Response.json([sample]);
      if(url.endsWith("/queries/manifest.json"))return new Response(null,{status:404});
      if(url.endsWith("/queries/"+digest+".json"))return Response.json({
        version:1,query_sha256:digest,dimension:512,dtype:"float16-le",vector:encoded,
      });
      if(url.endsWith("/manifest.json"))return Response.json(fixture.manifest);
      if(url.endsWith("/"+tail.path))return new Response(Uint8Array.from(
        Uint8Array.from(atob(fixture.tail_base64),c=>c.charCodeAt(0))
      ).buffer);
      if(url.endsWith("/"+tail.ids_path))return new Response(Uint8Array.from(
        Uint8Array.from(atob(fixture.tail_ids_base64),c=>c.charCodeAt(0))
      ).buffer);
      return new Response(null,{status:404});
    }));
    const result=await semanticSearch(request("198.51.100.4"),env,query,new URLSearchParams());
    expect(result.semantic).toBe("unavailable");
    expect(result.semantic_reason).toBe("no_semantic_match");
    expect(result.clips).toEqual([]);
    expect(result.scores).toBeUndefined();
  });
});
