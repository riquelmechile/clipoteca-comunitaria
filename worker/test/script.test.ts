import {beforeEach,describe,expect,it,vi} from "vitest";
import worker from "../src/index";
import {parseScript} from "../src/script";
import {resetSemanticCache,resetQueryDispatch} from "../src/semantic";
import {resetQuotaReservations} from "../src/quota";

const clip=(id:number,title:string,orientation="16:9",duration=12,ext=".mp4")=>({
  id:"clip-"+String(id).padStart(6,"0"),
  title:{es:title,en:title},description:{es:title,en:title},
  tags:{es:[title],en:[title]},
  source:{provider:"pexels",source_language:"es",provider_id:"p"+id,
    source_url:"https://pexels.example/video/"+id,source_title:title,
    source_start:0,source_end:duration},
  media:{aspect_ratio:orientation,duration_seconds:duration,width:1920,fps:30,has_audio:false},
  storage:{asset_url:"https://github.com/demo/releases/download/v1/"+id+ext,
    thumbnail_url:"https://github.com/demo/releases/download/v1/"+id+".webp",
    storyboard_urls:["https://github.com/demo/releases/download/v1/"+id+"-01.webp"]},
});
const scene=(id:string,query:string,orientation="16:9",min_duration=8)=>({
  id,narration:"Narration for "+id,query,orientation,min_duration,
});
const five=[scene("intro","agua"),scene("rain","lluvia"),scene("forest","bosque"),
  scene("city","ciudad"),scene("coast","olas")];
const catalog=[clip(1,"lluvia agua"),clip(2,"agua nieve"),clip(3,"bosque"),
  clip(4,"ciudad"),clip(5,"olas")];
const post=(scenes:unknown,ip="203.0.113.1")=>new Request("https://clipoteca.test/v1/script",{
  method:"POST",headers:{"content-type":"application/json","cf-connecting-ip":ip},
  body:JSON.stringify({scenes}),
});
const rpc=(name:string,args:unknown)=>new Request("https://clipoteca.test/mcp",{
  method:"POST",headers:{"content-type":"application/json",
    accept:"application/json, text/event-stream",host:"clipoteca.test",
    origin:"https://client.example"},
  body:JSON.stringify({jsonrpc:"2.0",method:"tools/call",id:1,
    params:{name,arguments:args}}),
});
const toolsList=()=>new Request("https://clipoteca.test/mcp",{
  method:"POST",headers:{"content-type":"application/json",
    accept:"application/json, text/event-stream",host:"clipoteca.test",
    origin:"https://client.example"},
  body:JSON.stringify({jsonrpc:"2.0",method:"tools/list",id:1,params:{}}),
});
const env={
  CATALOG_URL:"https://fixture.test/catalog",
  CATALOG_TTL_SECONDS:"0",
  CLIPOTECA_DISPATCH_TOKEN:"shared-application-token",
  MCP_ALLOWED_HOSTS:"clipoteca.test",
  MCP_ALLOWED_ORIGINS:"https://client.example",
};
function stubCatalog(videos:unknown[]=catalog) {
  const calls:{url:string;init?:RequestInit}[]=[];
  vi.stubGlobal("fetch",vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input instanceof Request?input.url:input);
    calls.push({url,...(init?{init}:{})});
    if(url===env.CATALOG_URL)return Response.json(videos);
    if(url.includes("/data/jobs/"))return new Response(null,{status:404});
    if(url.endsWith("/dispatches"))return new Response(null,{status:204});
    return new Response(null,{status:404});
  }));
  return calls;
}
async function rpcPayload(response:Response):Promise<any>{
  const raw=await response.text();
  return JSON.parse(raw.trimStart().startsWith("{")?raw:
    raw.match(/(?:^|\n)data: (.+)/m)?.[1]??"{}");
}
beforeEach(()=>{
  resetSemanticCache();resetQueryDispatch();resetQuotaReservations();
  const stored=new Map<string,Response>();
  vi.stubGlobal("caches",{default:{
    match:vi.fn(async(req:Request)=>stored.get(req.url)?.clone()),
    put:vi.fn(async(req:Request,res:Response)=>{stored.set(req.url,res.clone());}),
  }});
});

describe("M23 guion to clips REST",()=>{
  it("resolves five scenes from one catalogue without duplicate clips",async()=>{
    const calls=stubCatalog();
    const result=await worker.fetch(post(five),env);
    expect(result.status).toBe(200);
    const body=await result.json() as {
      script_id:string;scenes:Array<{id:string;status:string;
        media:{clip_id:string;url:string;storyboard_urls:string[];thumbnail_url:string;
          source:{provider:string;source_url:string};type:string}|null}>;
      summary:{matched:number;queued:number;missing:number};
    };
    expect(body.script_id).toMatch(/^[a-f0-9]{64}$/);
    expect(body.summary).toEqual({matched:5,queued:0,missing:0});
    expect(body.scenes.map(x=>x.status)).toEqual(["matched","matched","matched","matched","matched"]);
    expect(body.scenes.map(x=>x.media?.clip_id)).toEqual([
      "clip-000002","clip-000001","clip-000003","clip-000004","clip-000005",
    ]);
    expect(new Set(body.scenes.map(x=>x.media!.clip_id)).size).toBe(5);
    expect(body.scenes[0]?.media).toMatchObject({
      type:"video",thumbnail_url:"https://github.com/demo/releases/download/v1/2.webp",
      source:{provider:"pexels",source_url:"https://pexels.example/video/2"},
    });
    expect(body.scenes[0]?.media?.storyboard_urls).toHaveLength(1);
    expect(calls.filter(x=>x.url===env.CATALOG_URL)).toHaveLength(1);
    expect(calls.some(x=>x.url.endsWith("/dispatches"))).toBe(false);
  });

  it("returns mixed matched queued missing and shares existing import quota",async()=>{
    const calls=stubCatalog([catalog[0]]);
    const rows=[scene("one","lluvia"),scene("two","nieve"),scene("three","cielo")];
    const response=await worker.fetch(post(rows,"203.0.113.20"),env);
    expect(response.status).toBe(200);
    const body=await response.json() as {scenes:Array<{status:string;media:unknown;
      request_id?:string;reason?:string;job?:{request_id:string;status:string}}>;
      summary:{matched:number;queued:number;missing:number}};
    expect(body.scenes.map(x=>x.status)).toEqual(["matched","queued","missing"]);
    expect(body.scenes[1]?.request_id).toMatch(/^[a-f0-9]{64}$/);
    expect(body.scenes[1]?.job?.status).toBe("queued");
    expect(body.scenes[2]?.reason).toBe("rate_limited");
    expect(body.scenes[1]?.media).toBeNull();
    expect(body.summary).toEqual({matched:1,queued:1,missing:1});
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(1);
  });

  it("keeps deterministic script and job IDs on repeat without re-dispatch",async()=>{
    const calls=stubCatalog([]);
    const rows=[scene("first","nieve")];
    const first=await worker.fetch(post(rows),env);
    const second=await worker.fetch(post(rows),env);
    expect(first.status).toBe(200);
    expect(second.status).toBe(200);
    const a=await first.json() as {script_id:string;scenes:Array<{request_id:string;status:string}>};
    const b=await second.json() as typeof a;
    expect(a).toEqual(b);
    expect(a.scenes[0]?.status).toBe("queued");
    expect(calls.filter(x=>x.url.endsWith("/dispatches"))).toHaveLength(1);
  });

  it("enforces min duration and aspect ratio, never provides an image as video",async()=>{
    stubCatalog([clip(1,"nieve","9:16",5),clip(2,"nieve","16:9",15),
      clip(3,"nieve","9:16",15,".png")]);
    const response=await worker.fetch(post([scene("one","nieve","9:16",10)]),
      {...env,CLIPOTECA_DISPATCH_TOKEN:""});
    const body=await response.json() as {scenes:Array<{status:string;media:unknown;reason?:string}>};
    expect(body.scenes[0]?.status).toBe("missing");
    expect(body.scenes[0]?.media).toBeNull();
    expect(body.scenes[0]?.reason).toMatch(/^non_video_material:/);
  });

  it("bounds scene count, fields, body, duplicate IDs and methods",async()=>{
    stubCatalog();
    const wrong=[
      [], Array.from({length:51},(_,i)=>scene("s"+i,"agua")),
      [scene("a","agua"),scene("a","olas")],
      [{...scene("a","agua"),orientation:"up"}],
      [{...scene("a","agua"),min_duration:21}],
      [{...scene("a","agua"),unexpected:true}],
    ];
    for(const scenes of wrong)expect((await worker.fetch(post(scenes),env)).status).toBe(400);
    expect((await worker.fetch(new Request("https://clipoteca.test/v1/script"),env)).status).toBe(405);
    const huge=post([scene("a","x".repeat(70_000))]);
    expect((await worker.fetch(huge,env)).status).toBe(400);
    expect(()=>parseScript({scenes:[{...scene("a","agua"),id:"../../etc"}]})).toThrow();
  });

  it("preserves the script and marks each scene missing when catalogue fails",async()=>{
    vi.stubGlobal("fetch",vi.fn(async()=>new Response(null,{status:503})));
    const response=await worker.fetch(post(five),env);
    expect(response.status).toBe(200);
    const result=await response.json() as {scenes:Array<{status:string;reason:string}>};
    expect(result.scenes).toHaveLength(5);
    expect(result.scenes.every(x=>x.status==="missing"&&x.reason==="catalogue_unavailable")).toBe(true);
  });
});

describe("M23 typed MCP resolver",()=>{
  it("lists resolve_script with output schema and returns five scenes",async()=>{
    stubCatalog();
    const listed=await rpcPayload(await worker.fetch(toolsList(),env));
    const resolver=listed.result.tools.find((t:{name:string})=>t.name==="resolve_script");
    expect(resolver.outputSchema.properties).toHaveProperty("scenes");
    const result=await rpcPayload(await worker.fetch(rpc("resolve_script",{scenes:five}),env));
    expect(result.result.structuredContent.summary).toEqual({matched:5,queued:0,missing:0});
    expect(result.result.structuredContent.scenes[0].media.type).toBe("video");
    expect(result.result.isError).not.toBe(true);
  });
});
