import {beforeEach, afterEach, describe, expect, it, vi} from "vitest";
import worker from "../src/index";
import {inspectHeader, mediaUrl, videoDuration, PREFIX, maxBytes} from "../src/media-header";
import {quota, resetQuotaReservations} from "../src/quota";

const base="https://clipoteca.test";
const token="test-write-token-123456";
const env={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_ISSUE_TOKEN:"test-issues-token",
  CLIPOTECA_UPLOAD_TOKEN:"test-upload-token"};
const metadata={kind:"image",title:"Lluvia",author:"Autor",source:"Origen"};
const png=(w=100,h=100,size=24)=>{
  const b=new Uint8Array(size),v=new DataView(b.buffer);
  v.setUint32(0,0x89504e47);v.setUint32(4,0x0d0a1a0a);
  v.setUint32(8,13);v.setUint32(12,0x49484452);
  v.setUint32(16,w);v.setUint32(20,h);return b;
};
const mp4=(seconds:number)=>{
  const b=new Uint8Array(56),v=new DataView(b.buffer);
  v.setUint32(0,16);v.setUint32(4,0x66747970);v.setUint32(8,0x69736f6d);
  v.setUint32(16,40);v.setUint32(20,0x6d6f6f76);
  v.setUint32(24,32);v.setUint32(28,0x6d766864);
  v.setUint32(44,1000);v.setUint32(48,seconds*1000);
  return b;
};
const post=(path:string,body:unknown,auth=true)=>new Request(base+path,{
  method:"POST",headers:{"content-type":"application/json",...(auth?{authorization:"Bearer "+token}:{})},
  body:JSON.stringify(body),
});
function fakeCache(){
  const data=new Map<string,Response>();
  vi.stubGlobal("caches",{default:{
    match:vi.fn(async (req:Request)=>data.get(req.url)?.clone()),
    put:vi.fn(async (req:Request,value:Response)=>{data.set(req.url,value.clone());}),
  }});
}
describe("media intake",()=>{
  beforeEach(()=>{resetQuotaReservations();fakeCache();});
  afterEach(()=>vi.unstubAllGlobals());
  it("requires direct public URLs and validates magic and size metadata",()=>{
    expect(mediaUrl("https://cdn.example.com/test.png","image")).toBe("https://cdn.example.com/test.png");
    for(const url of ["http://cdn.example.com/x.png","https://127.0.0.1/x.png",
      "https://[::1]/x.png","https://user:pass@cdn.example.com/x.png",
      "https://cdn.example.com/x.png?secret=x","https://localhost/x.png",
      "https://cdn.example.com/x.mp4"]){
      expect(()=>mediaUrl(url,"image")).toThrow();
    }
    expect(inspectHeader(png(),"image")).toBe("image/png");
    expect(()=>inspectHeader(png(6000,5000),"image")).toThrow("image_dimensions");
    expect(()=>inspectHeader(new Uint8Array(30),"image")).toThrow();
    expect(inspectHeader(mp4(120),"video")).toBe("video/mp4");
    expect(videoDuration(mp4(120),"video/mp4")).toBe(120);
    expect(videoDuration(mp4(121),"video/mp4")).toBe(121);
    expect(videoDuration(mp4(20).subarray(0,16),"video/mp4")).toBeNull();
    expect(maxBytes("image")).toBe(10*1024*1024);
    expect(maxBytes("video")).toBe(50*1024*1024);
  });
  it("requires write auth and explains disabled uploads without touching upstream",async()=>{
    const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
    expect((await worker.fetch(post("/v1/media",metadata,false),env)).status).toBe(401);
    const disabled={CLIPOTECA_WRITE_TOKEN:token};
    const res=await worker.fetch(post("/v1/media/upload",metadata),disabled);
    expect(res.status).toBe(503);expect(await res.json()).toMatchObject({error:{code:"uploads_not_configured"}});
    const page=await worker.fetch(new Request(base+"/v1/media/upload"),disabled);
    expect(page.status).toBe(503);expect(await page.text()).toContain("queda público");
    const enabled=await worker.fetch(new Request(base+"/v1/media/upload"),env);
    expect(enabled.status).toBe(200);expect(await enabled.text()).toContain('type="file"');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("limits daily count and bytes per IP",async()=>{
    const request=new Request(base,{headers:{"cf-connecting-ip":"203.0.113.1"}});
    for(let i=0;i<4;i++)expect(await quota(request,"media",50*1024*1024)).toBe(true);
    expect(await quota(request,"media",1)).toBe(false);
    const other=new Request(base,{headers:{"cf-connecting-ip":"203.0.113.2"}});
    for(let i=0;i<20;i++)expect(await quota(other,"media",1)).toBe(true);
    expect(await quota(other,"media",1)).toBe(false);
  });
  it("registers a range-probed URL without the upload token; retries are idempotent",async()=>{
    let issue:Record<string,unknown>|undefined;
    const bytes=png();
    const fetcher=vi.fn(async (url:string|URL|Request,init?:RequestInit)=>{
      const u=new URL(String(url));
      if(u.hostname==="cdn.example.com"){
        expect(new Headers(init?.headers).get("range")).toBe("bytes=0-"+(PREFIX-1));
        expect(init?.redirect).toBe("manual");
        return new Response(bytes,{status:206,headers:{"content-range":"bytes 0-23/24","content-length":"24"}});
      }
      if(u.pathname==="/search/issues")return Response.json({items:[]});
      if(init?.method==="POST"){
        issue={...JSON.parse(String(init.body)),number:42,author_association:"OWNER"};
        return Response.json(issue,{status:201});
      }
      if(u.pathname.endsWith("/issues/42") && new Headers(init?.headers).get("authorization")!=="Bearer "+env.CLIPOTECA_ISSUE_TOKEN) return new Response(null,{status:403});
      return Response.json(issue);
    });
    vi.stubGlobal("fetch",fetcher);
    const body={...metadata,url:"https://cdn.example.com/a.png"};
    const without={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_ISSUE_TOKEN:env.CLIPOTECA_ISSUE_TOKEN};
    const response=await worker.fetch(post("/v1/media",body),without);
    expect(response.status).toBe(201);
    const record=await response.json();
    expect(record).toMatchObject({media_id:"u2a",bytes:24,mime:"image/png",status:"registered"});
    expect(record).not.toHaveProperty("registry");
    expect(await (await worker.fetch(post("/v1/media",body),without)).json()).toEqual(record);
    expect(await (await worker.fetch(new Request(base+"/v1/media/u2a"),without)).json()).toEqual(record);
    expect(fetcher.mock.calls.filter(c=>c[1]?.method==="POST")).toHaveLength(1);
  });
  it("rejects oversized, redirected, and over-duration remote files",async()=>{
    const data=mp4(121);
    for(const mode of ["size","redirect","duration"]){
      vi.stubGlobal("fetch",vi.fn(async (url:string)=>{
        if(url.includes("/search/issues"))return Response.json({items:[]});
        if(mode==="redirect")return new Response(null,{status:302,headers:{location:"https://127.0.0.1"}});
        return new Response(data,{headers:{"content-length":String(mode==="size"?maxBytes("video")+1:data.length)}});
      }));
      const r=await worker.fetch(post("/v1/media",{...metadata,kind:"video",url:"https://cdn.example.com/a.mp4"}),env);
      expect(r.status).toBe(400);
    }
  });
  it("registers Wikimedia WebM and JPEG using the real URL paths and partial response headers",async()=>{
    const videoUrl="https://upload.wikimedia.org/wikipedia/commons/1/16/Waves_at_Hikkaduwa_beach%2C_Sri_Lanka.webm";
    const imageUrl="https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg";
    const webm=new Uint8Array(PREFIX);
    // Minimal EBML header and Segment Info containing a 15-second duration.
    const header=new Uint8Array([0x1a,0x45,0xdf,0xa3,0x87,0x42,0x82,0x84,0x77,0x65,0x62,0x6d,
      0x18,0x53,0x80,0x67,0xff,0x15,0x49,0xa9,0x66,0x87,0x44,0x89,0x84,0,0,0,0]);
    new DataView(header.buffer).setFloat32(25,15000);
    webm.set(header);
    const jpeg=new Uint8Array(4096);
    jpeg.set([0xff,0xd8,0xff,0xc0,0,11,8,0,100,0,100,1,1,0x11,0]);
    const userAgent="ClipotecaComunitaria/3.0 (+https://github.com/riquelmechile/clipoteca-comunitaria)";
    let issueNumber=50;
    const fetcher=vi.fn(async (url:string|URL|Request,init?:RequestInit)=>{
      const u=new URL(String(url));
      if(u.hostname==="upload.wikimedia.org"){
        const headers=new Headers(init?.headers);
        if(headers.get("user-agent")!==userAgent)return new Response("UA required",{status:403});
        expect(headers.get("range")).toBe("bytes=0-"+(PREFIX-1));
        expect(headers.get("accept-encoding")).toBe("identity");
        expect(headers.get("authorization")).toBeNull();
        expect(init?.redirect).toBe("manual");
        const isVideo=u.href===videoUrl;
        expect(u.href).toBe(isVideo?videoUrl:imageUrl);
        const body=isVideo?webm:jpeg;
        const total=isVideo?1572864:jpeg.length;
        return new Response(body,{status:206,headers:{
          "content-range":"bytes 0-"+(body.length-1)+"/"+total,
          "content-length":String(body.length),
          "content-type":isVideo?"video/webm":"image/jpeg",
        }});
      }
      if(u.pathname==="/search/issues")return Response.json({items:[]});
      return Response.json({...JSON.parse(String(init?.body)),number:issueNumber++,author_association:"OWNER"},{status:201});
    });
    vi.stubGlobal("fetch",fetcher);
    for(const [url,kind,mime,bytes] of [[videoUrl,"video","video/webm",1572864],[imageUrl,"image","image/jpeg",4096]] as const){
      const response=await worker.fetch(post("/v1/media",{...metadata,kind,url,
        source:"https://commons.wikimedia.org/wiki/File:Example"}),env);
      expect(response.status).toBe(201);
      expect(await response.json()).toMatchObject({url,kind,mime,bytes,status:"registered",
        ...(kind==="video"?{duration_seconds:15}:{})});
    }
  });
  it("returns specific safe errors for upstream failures and invalid media",async()=>{
    const url="https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg";
    const remote=(make:()=>Response)=>{
      vi.stubGlobal("fetch",vi.fn(async (target:string)=>{
        if(target.includes("/search/issues"))return Response.json({items:[]});
        return make();
      }));
    };
    const cases:[string,()=>Response][]=[
      ["source_status_403",()=>new Response("private URL?token=secret",{status:403})],
      ["source_status_302",()=>new Response(null,{status:302,headers:{location:"https://127.0.0.1"}})],
      ["invalid_range",()=>new Response(png(),{status:206,headers:{"content-range":"bytes 1-24/25","content-length":"24"}})],
      ["invalid_range",()=>new Response(png(),{status:206,headers:{"content-range":"bytes 0-23/24","content-length":"23"}})],
      ["media_too_large",()=>new Response(png(),{headers:{"content-length":String(maxBytes("image")+1)}})],
      ["image_dimensions",()=>new Response(png(6000,5000),{headers:{"content-length":"24"}})],
      ["invalid_media_header",()=>new Response(new Uint8Array(24),{headers:{"content-length":"24"}})],
      ["truncated_source",()=>new Response(png(),{headers:{"content-length":"25"}})],
    ];
    for(const [code,make] of cases){
      remote(make);
      const r=await worker.fetch(post("/v1/media",{...metadata,url}),env);
      expect(r.status).toBe(400);
      const body=await r.json() as {error:{code:string;message:string}};
      expect(body.error.code).toBe(code);
      expect(body.error.message).not.toBe(code);
      expect(JSON.stringify(body)).not.toMatch(/token=secret|127\.0\.0\.1|wikimedia\.org/);
    }
    remote(()=>new Response(mp4(121),{headers:{"content-length":"56"}}));
    const duration=await worker.fetch(post("/v1/media",{...metadata,kind:"video",url:"https://cdn.example.com/a.mp4"}),env);
    expect(await duration.json()).toMatchObject({error:{code:"video_duration_unknown_or_exceeds_120s"}});
    const badUrl=await worker.fetch(post("/v1/media",{...metadata,url:url+"?token=secret"}),env);
    expect(await badUrl.json()).toMatchObject({error:{code:"invalid_url"}});
    remote(()=>{throw new TypeError("fetch failed: https://example.com/?token=secret");});
    const network=await worker.fetch(post("/v1/media",{...metadata,url}),env);
    expect(network.status).toBe(502);
    expect(await network.json()).toEqual({error:{code:"source_unreachable",message:"No se pudo conectar con el origen."}});
  });
  it("accepts a complete 206 range when the upstream omits Content-Length",async()=>{
    vi.stubGlobal("fetch",vi.fn(async (url:string,init?:RequestInit)=>{
      if(url.includes("/search/issues"))return Response.json({items:[]});
      if(init?.method==="POST")return Response.json({...JSON.parse(String(init.body)),number:60,author_association:"OWNER"},{status:201});
      return new Response(png(),{status:206,headers:{"content-range":"bytes 0-23/24"}});
    }));
    const r=await worker.fetch(post("/v1/media",{...metadata,url:"https://cdn.example.com/short.png"}),env);
    expect(r.status).toBe(201);
    expect(await r.json()).toMatchObject({bytes:24,mime:"image/png"});
  });
  it("streams a bounded prefix and the rest to GitHub, saves a manifest, and refuses a second write",async()=>{
    class TestFixedLengthStream extends TransformStream<Uint8Array,Uint8Array>{
      constructor(_size:number){super();}
    }
    vi.stubGlobal("FixedLengthStream",TestFixedLengthStream);
    const records=new Map<string,{name:string;size:number;state:string;browser_download_url:string}>();
    let manifest="", uploaded=0;
    const bytes=png(100,100,PREFIX+4096);
    const fetcher=vi.fn(async (url:string|URL|Request,init?:RequestInit)=>{
      const u=new URL(String(url));
      if(u.hostname==="github.com")return new Response(manifest);
      if(u.hostname==="uploads.github.com"){
        const name=u.searchParams.get("name")!;
        if(records.has(name))return new Response(null,{status:422});
        let size=0;
        if(typeof init?.body==="string"){
          manifest=init.body;size=new TextEncoder().encode(manifest).length;
        }else{
          expect(init?.body).toBeInstanceOf(ReadableStream);
          const reader=(init!.body as ReadableStream<Uint8Array>).getReader();
          let chunks=0;
          for(;;){const next=await reader.read();if(next.done)break;size+=next.value.length;chunks++;}
          expect(chunks).toBeGreaterThan(1);uploaded++;
        }
        expect(size).toBe(Number(new Headers(init?.headers).get("content-length")));
        const value={name,size,state:"uploaded",browser_download_url:"https://github.com/file"};
        records.set(name,value);return Response.json(value,{status:201});
      }
      if(u.pathname.endsWith("/assets"))return Response.json([...records.values()]);
      return Response.json({id:1,assets:[]});
    });
    vi.stubGlobal("fetch",fetcher);
    const created=await worker.fetch(post("/v1/media/upload",metadata),env);
    expect(created.status).toBe(201);
    const t=await created.json() as {media_id:string;put_url:string;upload_token:string;upload_url:string};
    expect(t.upload_url).toContain("#");
    const request=()=>new Request(t.put_url,{method:"PUT",headers:{
      authorization:"Bearer "+t.upload_token,"content-length":String(bytes.length),
    },body:bytes});
    const first=await worker.fetch(request(),env);
    expect(first.status).toBe(201);
    const record=await first.json();
    expect(record).toMatchObject({media_id:t.media_id,bytes:bytes.length,origin:"upload"});
    expect(await (await worker.fetch(request(),env)).json()).toEqual(record);
    expect(uploaded).toBe(1);
    const bad=new Request(t.put_url,{method:"PUT",headers:{authorization:"Bearer "+t.upload_token+"x","content-length":"24"},body:png()});
    expect((await worker.fetch(bad,env)).status).toBe(401);
    expect(await (await worker.fetch(new Request(base+"/v1/media/"+t.media_id),env)).json()).toEqual(record);
  });
});
