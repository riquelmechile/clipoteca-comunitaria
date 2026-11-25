import {beforeEach, describe, expect, it, vi} from "vitest";
import worker from "../src/index";
import {quota, resetQuotaReservations, VIDEO_DAILY_LIMIT} from "../src/quota";
import {zipEntry} from "../src/video-zip";

const id = "a".repeat(32);
async function workflowId(value:string) {
  const hash=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value));
  return Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,"0")).join("");
}
const token = "write-test-token-1234";
const env = {CLIPOTECA_WRITE_TOKEN:token, CLIPOTECA_DISPATCH_TOKEN:"actions-test-token"};
const base = "https://clipoteca.test";
const script = {title:"Water",aspect:"9:16",scenes:[{},{},{}]};
function request(path:string, body?:unknown, auth=true) {
  return new Request(base+path, body===undefined ? {} : {
    method:"POST", headers:{"content-type":"application/json",
      ...(auth ? {authorization:"Bearer "+token} : {})}, body:JSON.stringify(body),
  });
}
function zip(name:string, data:Uint8Array, deflate=false) {
  const n = new TextEncoder().encode(name);
  // Raw DEFLATE stored block; method 8 is still exercised without external dependencies.
  const packed = deflate ? Uint8Array.from([1,data.length&255,data.length>>8,
    (~data.length)&255,((~data.length)>>8)&255,...data]) : data;
  const local = 30+n.length+packed.length;
  const central = 46+n.length;
  const buffer = new ArrayBuffer(local+central+22);
  const v = new DataView(buffer), bytes = new Uint8Array(buffer);
  const u16 = (o:number,x:number)=>v.setUint16(o,x,true);
  const u32 = (o:number,x:number)=>v.setUint32(o,x,true);
  u32(0,0x04034b50); u16(4,20); u16(8,deflate?8:0);
  u32(18,packed.length); u32(22,data.length); u16(26,n.length);
  bytes.set(n,30); bytes.set(packed,30+n.length);
  u32(local,0x02014b50); u16(local+6,20); u16(local+10,deflate?8:0);
  u32(local+20,packed.length); u32(local+24,data.length); u16(local+28,n.length);
  bytes.set(n,local+46);
  const end = local+central;
  u32(end,0x06054b50); u16(end+8,1); u16(end+10,1); u32(end+12,central); u32(end+16,local);
  return buffer;
}
const encode = (text:string)=>new TextEncoder().encode(text);
function fakeActions(options:{expired?:boolean; status?:string; conclusion?:string;
  file?:boolean; deflate?:boolean; gone?:boolean; invalidEvidence?:boolean; large?:boolean;
  fallback?:boolean}={}) {
  const movie = zip("video.mp4",options.large ? new Uint8Array(9_000_000) : encode("video bytes"),options.deflate);
  const delivery = zip("entrega.json",encode(options.invalidEvidence ? "invalid JSON" : JSON.stringify({
    qa:{pass:true,failed:[]}, clips_indexed:["clip-000001"],
    ...(options.fallback ? {warnings:[{scene_id:"chiloe",degraded_reason:"low_sim"}]} : {}),
  })));
  const fn = vi.fn(async (url:string|URL|Request, init?:RequestInit) => {
    const path = new URL(String(url)).pathname;
    const rid = await workflowId(id);
    if (path.endsWith("/runs")) return Response.json({workflow_runs:[{
      id:4, display_title:"render "+rid,status:options.status??"completed",
      conclusion:options.conclusion??"success",
    }]});
    if (path.endsWith("/jobs")) return Response.json({jobs:[
      {name:"plan",status:"completed",conclusion:"success"},
      {name:"scene (intro)",status:"completed",conclusion:"success"},
    ]});
    if (path.endsWith("/artifacts")) return Response.json({artifacts:options.file===false?[]:[
      {id:7,name:"video-"+rid,expired:options.expired??false,size_in_bytes:movie.byteLength},
      {id:8,name:"entrega-"+rid,expired:false,size_in_bytes:delivery.byteLength},
    ]});
    if (path.endsWith("/8/zip")) return new Response(delivery);
    if (path.endsWith("/7/zip") && options.gone) return new Response(null,{status:410});
    if (path.endsWith("/7/zip")) return new Response(null,{status:302,
      headers:{location:"https://example.blob.core.windows.net/delivery?signature=private"}});
    if (path==="/delivery") {
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      const range=new Headers(init?.headers).get("range");
      if(!range) return new Response(movie,{headers:{"content-length":String(movie.byteLength)}});
      const match=/^bytes=(\d*)-(\d+)$/.exec(range);
      if(!match) throw new Error("unexpected range");
      const start=match[1]?Number(match[1]):Math.max(0,movie.byteLength-Number(match[2]));
      const end=match[1]?Number(match[2]):movie.byteLength-1;
      return new Response(movie.slice(start,end+1),{status:206,headers:{
        "content-range":"bytes "+start+"-"+end+"/"+movie.byteLength,
        "content-length":String(end-start+1),
      }});
    }
    throw new Error("unexpected upstream path");
  });
  vi.stubGlobal("fetch",fn);
  return fn;
}

describe("video REST delivery",()=>{
  beforeEach(()=>{
    resetQuotaReservations();
    const saved = new Map<string,Response>();
    vi.stubGlobal("caches",{default:{
      match:async (key:Request)=>saved.get(key.url),
      put:async (key:Request,value:Response)=>{saved.set(key.url,value);},
    }});
  });
  it("validates authorization, script and quota; dispatches random capability",async()=>{
    let dispatched = "";
    const fetcher = vi.fn(async (_url:string, init?:RequestInit)=> {
      const data = JSON.parse(String(init?.body));
      expect(data.ref).toBe("main");
      expect(data.inputs.request_id).toMatch(/^[a-f0-9]{64}$/);
      dispatched = data.inputs.request_id;
      expect(JSON.parse(data.inputs.script_json)).toEqual(script);
      expect(data.inputs.index_clips).toBe("false");
      return new Response(null,{status:204});
    });
    vi.stubGlobal("fetch",fetcher);
    expect((await worker.fetch(request("/v1/videos",{script},false),env)).status).toBe(401);
    expect((await worker.fetch(request("/v1/videos",{script:{}}),env)).status).toBe(400);
    const res=await worker.fetch(request("/v1/videos",{script,index_clips:false}),env);
    expect(res.status).toBe(202);
    const result = await res.json() as {status:string;id:string};
    expect(result).toMatchObject({status:"queued",id:expect.stringMatching(/^[a-f0-9]{32}$/)});
    expect(dispatched).toBe(await workflowId(result.id));
    expect(dispatched).not.toBe(result.id);
    expect((await worker.fetch(request("/v1/videos",{script}),env)).status).toBe(429);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("prevalidates contributions and pinned clips without dispatching invalid refs",async()=>{
    const fetcher=vi.fn(async (url:string|URL|Request, init?:RequestInit)=>{
      const address=url instanceof Request?url.url:String(url);
      if(address.includes("/issues/119") || address.includes("/issues/120")){
        expect(new Headers(init?.headers).get("authorization")).toBe("Bearer issue-test");
        const video=address.endsWith("/119");
        return Response.json({number:video?119:120,author_association:"OWNER",
          title:"[media-v1] "+"a".repeat(64),
          body:JSON.stringify({registry:"clipoteca-media-v1",origin:"url",status:"registered",kind:video?"video":"image"})});
      }
      if(address.includes("/issues/121")) return new Response(null,{status:404});
      if(address.includes("/data/clips.json")) return Response.json([
        {id:"clip-000001",status:"published"},{id:"clip-000002",status:"retired"},
      ]);
      if(address.endsWith("/dispatches")) return new Response(null,{status:204});
      throw new Error("unexpected upstream");
    });
    vi.stubGlobal("fetch",fetcher);
    const configured={...env,CLIPOTECA_ISSUE_TOKEN:"issue-test"};
    const body=(ref:string)=>({script:{...script,scenes:[{media:{ref}},{},{}]}});
    for(const [ref,code] of [["asset:unknown","asset_not_found"],["u79","media_not_found"],
      ["clip-000002","clip_not_found"],["bad","invalid_media_ref"]]){
      const response=await worker.fetch(request("/v1/videos",body(ref!)),configured);
      expect(response.status).toBeGreaterThanOrEqual(400);
      expect(await response.json()).toMatchObject({error:{code}});
    }
    const invalidWindow=await worker.fetch(request("/v1/videos",{script:{...script,
      scenes:[{media:{ref:"u78",start:0}},{},{}]}}),configured);
    expect(await invalidWindow.json()).toMatchObject({error:{code:"invalid_image_window"}});
    expect(fetcher.mock.calls.some(c=>String(c[0]).endsWith("/dispatches"))).toBe(false);
    const response=await worker.fetch(request("/v1/videos",{script:{...script,
      scenes:[{media:{ref:"u78",motion:"zoom_in"}},{media:{ref:"clip-000001"}},{media:{ref:"asset:clipoteca-pipeline"}}]}}),configured);
    expect(response.status).toBe(202);
  });
  it.each(["queued","in_progress","completed"])("reports %s",async(state)=>{
    fakeActions({status:state});
    const res=await worker.fetch(request("/v1/videos/"+id),env);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({id,status:"done", stages:{scenes:{ready:1,total:1}},
      qa:{pass:true,failed:[]},clips_indexed:["clip-000001"]});
  });
  it.each(["queued","in_progress"])("reports %s before delivery",async(state)=>{
    fakeActions({status:state,file:false});
    const res=await worker.fetch(request("/v1/videos/"+id),env);
    expect(await res.json()).toMatchObject({status:state==="queued"?"queued":"running"});
  });
  it("reports failed and expired",async()=>{
    fakeActions({conclusion:"failure",file:false});
    expect(await (await worker.fetch(request("/v1/videos/"+id),env)).json())
      .toMatchObject({status:"failed"});
    fakeActions({expired:true});
    expect(await (await worker.fetch(request("/v1/videos/"+id),env)).json())
      .toMatchObject({status:"expired"});
    expect((await worker.fetch(request("/v1/videos/"+id+"/file"),env)).status).toBe(410);
  });
  it.each([false,true])("delivers stored or deflated MP4: %s",async(deflate)=>{
    fakeActions({deflate});
    const res=await worker.fetch(request("/v1/videos/"+id+"/file"),env);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("video/mp4");
    expect(res.headers.get("content-length")).toBe("11");
    expect(await res.text()).toBe("video bytes");
  });
  it("rejects malformed capability before remote access",async()=>{
    const fetcher=fakeActions();
    expect((await worker.fetch(request("/v1/videos/123"),env)).status).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects oversized or missing zip entry",async()=>{
    const b=zip("video.mp4",encode("test"));
    await expect(zipEntry(b,"video.mp4",1)).rejects.toThrow();
    await expect(zipEntry(b,"other.mp4",10)).rejects.toThrow();
  });
});

describe("streaming and isolated video quota",()=>{
  it("streams only requested bytes of large stored ZIP member",async()=>{
    const fn=fakeActions({large:true});
    const req=new Request(base+"/v1/videos/"+id+"/file",{headers:{range:"bytes=10-19"}});
    const res=await worker.fetch(req,env);
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe("bytes 10-19/9000000");
    expect((await res.arrayBuffer()).byteLength).toBe(10);
    const blobCalls=fn.mock.calls.filter(([url])=>String(url).includes(".blob.core.windows.net"));
    expect(blobCalls.length).toBeGreaterThan(1);
    expect(blobCalls.every(([,init])=>new Headers(init?.headers).has("range"))).toBe(true);
  });
  it("returns 416 for unsatisfiable client range",async()=>{
    fakeActions();
    const req=new Request(base+"/v1/videos/"+id+"/file",{headers:{range:"bytes=100-200"}});
    const res=await worker.fetch(req,env);
    expect(res.status).toBe(416);
    expect(res.headers.get("content-range")).toBe("bytes */11");
  });
  it("maps upstream 410 and reports invalid evidence without losing state",async()=>{
    fakeActions({gone:true});
    expect((await worker.fetch(request("/v1/videos/"+id+"/file"),env)).status).toBe(410);
    expect(await (await worker.fetch(request("/v1/videos/"+id),env)).json())
      .toMatchObject({status:"expired"});
    fakeActions({invalidEvidence:true});
    expect(await (await worker.fetch(request("/v1/videos/"+id),env)).json())
      .toMatchObject({status:"done",warnings:["delivery_evidence_unavailable"]});
  });
  it("exposes scene warnings without failing QA or changing operational warnings",async()=>{
    fakeActions({fallback:true});
    const response = await worker.fetch(request("/v1/videos/"+id),env);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      status:"done",
      qa:{pass:true,failed:[],warnings:[{scene_id:"chiloe",degraded_reason:"low_sim"}]},
    });
  });
  it("uses a distinct five-minute reservation and daily cap",async()=>{
    resetQuotaReservations();
    const saved=new Map<string,Response>();
    vi.stubGlobal("caches",{default:{
      match:async(key:Request)=>saved.get(key.url)?.clone(),
      put:async(key:Request,value:Response)=>{saved.set(key.url,value.clone());},
    }});
    const req=new Request(base,{headers:{"cf-connecting-ip":"192.0.2.42"}});
    expect(await quota(req,"import")).toBe(true);
    expect(await quota(req,"video")).toBe(true);
    expect(await quota(req,"video")).toBe(false);
    const cooldown=[...saved.keys()].find(k=>/^\/_quota\/video\/[a-f0-9]{24}$/.test(new URL(k).pathname))!;
    expect(saved.get(cooldown)?.headers.get("cache-control")).toBe("public, max-age=300");
    expect(VIDEO_DAILY_LIMIT).toBe(50);
    for(let i=1;i<VIDEO_DAILY_LIMIT;i++){saved.delete(cooldown);expect(await quota(req,"video")).toBe(true);}
    saved.delete(cooldown);
    expect(await quota(req,"video")).toBe(false);
  });
  it("dispatches default index_clips as a string",async()=>{
    resetQuotaReservations();
    vi.stubGlobal("caches",{default:{match:async()=>undefined,put:async()=>{}}});
    vi.stubGlobal("fetch",vi.fn(async (_url:string,init?:RequestInit)=>{
      expect(JSON.parse(String(init?.body)).inputs.index_clips).toBe("true");
      return new Response(null,{status:204});
    }));
    expect((await worker.fetch(request("/v1/videos",{script}),env)).status).toBe(202);
  });
});
