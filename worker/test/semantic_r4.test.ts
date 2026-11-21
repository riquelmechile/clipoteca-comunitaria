import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import fixture from "../../tests/fixtures/m22/semantic_vectors.json";
import {loadCorpus,resetSemanticCache,resetQueryDispatch,semanticSearch} from "../src/semantic";

const env = {
  CATALOG_URL:"https://clipoteca.test/catalog",
  CATALOG_TTL_SECONDS:"0",
  CLIPOTECA_GITHUB_REPO:"riquelmechile/clipoteca-comunitaria",
  SEMANTIC_SEARCH:"1",
  CLIPOTECA_QUERY_DISPATCH_MODE:"1",
  CLIPOTECA_DISPATCH_TOKEN:"testing-token-allowed",
};
const rain = {
  id:"clip-000101",title:{es:"Lluvia",en:"Rain"},
  description:{es:"Agua",en:"Water"},tags:{es:["lluvia"],en:["rain"]},
  source:{provider:"pexels",source_language:"es"},
  media:{aspect_ratio:"16:9",duration_seconds:12,width:1920,fps:30,has_audio:false},
  storage:{asset_url:"https://github.com/test/clip.mp4"},
};
const binary = (encoded:string) => new Response(
  Uint8Array.from(atob(encoded),char=>char.charCodeAt(0)).buffer,
);
beforeEach(()=>{
  resetSemanticCache();resetQueryDispatch();
  vi.stubGlobal("caches",{default:{match:vi.fn(async()=>undefined),
    put:vi.fn(async()=>undefined)}});
});
afterEach(()=>vi.restoreAllMocks());

describe("M22 r4 transient corpus recovery",()=>{
  it("does not retain a corrupt/empty shard corpus for 20 minutes",async()=>{
    let now=1_700_000_000_000;
    vi.spyOn(Date,"now").mockImplementation(()=>now);
    let blobAttempts=0;
    const tail=fixture.manifest.tail!;
    vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input instanceof Request?input.url:input);
      if(url.endsWith("/manifest.json"))return Response.json(fixture.manifest);
      if(url.endsWith("/"+tail.ids_path))return binary(fixture.tail_ids_base64);
      if(url.endsWith("/"+tail.path)){
        blobAttempts++;
        return blobAttempts===1?new Response(null,{status:503}):binary(fixture.tail_base64);
      }
      return new Response(null,{status:404});
    }));
    expect((await loadCorpus(env)).size).toBe(0);
    now+=61_000;
    expect((await loadCorpus(env)).size).toBe(2);
    expect(blobAttempts).toBe(2);
  });

  it("returns empty_corpus without starting query_embedding Actions",async()=>{
    const calls:string[]=[];
    vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input instanceof Request?input.url:input);
      calls.push(url);
      if(url===env.CATALOG_URL)return Response.json([rain]);
      if(url.endsWith("/data/embeddings/manifest.json"))return Response.json({
        version:3,dimension:512,dtype:"float16-le",shard_capacity:4096,
        seal_capacity:256,shards:[],tail:null,
      });
      return new Response(null,{status:404});
    }));
    const page=await semanticSearch(
      new Request("https://clipoteca.test/v1/search"),env,"aguacero",
      new URLSearchParams(),true,
    );
    expect(page.semantic).toBe("unavailable");
    expect(page.semantic_reason).toBe("empty_corpus");
    expect(calls.some(x=>x.endsWith("/dispatches"))).toBe(false);
    expect(calls.some(x=>x.includes("/queries/"))).toBe(false);
  });
});
