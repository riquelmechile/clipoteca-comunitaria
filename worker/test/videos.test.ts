import {beforeEach, describe, expect, it, vi} from "vitest";
import worker from "../src/index";
import {resetQuotaReservations} from "../src/quota";
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
  file?:boolean; deflate?:boolean}={}) {
  const movie = zip("video.mp4",encode("video bytes"),options.deflate);
  const delivery = zip("entrega.json",encode(JSON.stringify({
    qa:{pass:true,failed:[]}, clips_indexed:["clip-000001"],
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
    if (path.endsWith("/7/zip")) return new Response(null,{status:302,
      headers:{location:"https://example.blob.core.windows.net/delivery?signature=private"}});
    if (path==="/delivery") {
      expect(new Headers(init?.headers).has("authorization")).toBe(false);
      return new Response(movie);
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
  it.each(["queued","in_progress","completed"])("reports %s",async(state)=>{
    fakeActions({status:state});
    const res=await worker.fetch(request("/v1/videos/"+id),env);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({id,status:state==="in_progress"?"running":
      state==="completed"?"done":"queued", stages:{scenes:{ready:1,total:1}},
      qa:{pass:true,failed:[]},clips_indexed:["clip-000001"]});
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
