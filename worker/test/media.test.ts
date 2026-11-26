import {beforeEach, afterEach, describe, expect, it, vi} from "vitest";
import worker from "../src/index";
import {inspectHeader, mediaUrl, videoDuration, PREFIX, JPEG_HEADER_LIMIT, maxBytes} from "../src/media-header";
import {batchTransition,type BatchState} from "../src/upload-batch-control";
import {quota, resetQuotaReservations} from "../src/quota";
import {CHUNK_BYTES, UPLOAD_TTL, transition, stage, actionsRun, type State, type Manifest} from "../src/upload-control";

const base="https://clipoteca.test";
const token="test-write-token-123456";
const env={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_ISSUE_TOKEN:"test-issues-token",
  CLIPOTECA_UPLOAD_TOKEN:"test-upload-token"};
const descriptive={description:{es:"Lluvia sobre el campo",en:"Rain in a field"},tags:{es:["lluvia"],en:["rain"]}};
const metadata={kind:"image",title:"Lluvia",author:"Autor",source:"Origen",...descriptive};
const png=(w=100,h=100,size=24)=>{
  const b=new Uint8Array(size),v=new DataView(b.buffer);
  v.setUint32(0,0x89504e47);v.setUint32(4,0x0d0a1a0a);
  v.setUint32(8,13);v.setUint32(12,0x49484452);
  v.setUint32(16,w);v.setUint32(20,h);return b;
};
const jpegWithApps=(sizes:number[],w=4160,h=3120,total=0)=>{
  const end=2+sizes.reduce((n,size)=>n+size+2,0)+13;
  const b=new Uint8Array(Math.max(end,total)),v=new DataView(b.buffer);
  b.set([0xff,0xd8]);let p=2;
  for(const [i,size] of sizes.entries()){
    b.set([0xff,i===0?0xe1:0xe6],p);v.setUint16(p+2,size);p+=size+2;
  }
  b.set([0xff,0xc0,0,11,8,0,0,0,0,1,1,0x11,0],p);
  v.setUint16(p+5,h);v.setUint16(p+7,w);
  return b;
};
const phoneApps=[31767,60000,60000,60000,60000,60000];
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
    expect(maxBytes("video")).toBe(100*1024*1024);
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
        expect(new Headers(init?.headers).get("range")).toBe("bytes=0-"+(JPEG_HEADER_LIMIT-1));
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
        expect(headers.get("range")).toBe("bytes=0-"+((u.href===videoUrl?PREFIX:JPEG_HEADER_LIMIT)-1));
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
  it("registers phone JPEG metadata beyond 128 KiB and bounds the URL stream",async()=>{
    const normal=jpegWithApps([],100,100,4096);
    const phone=jpegWithApps(phoneApps,4160,3120,3088386);
    const oversized=jpegWithApps(phoneApps,6000,5000);
    const tooLong=jpegWithApps(Array(34).fill(65000));
    let data=phone,read=0,issue=70;
    for(const [bytes,code,status,partial] of [
      [phone,null,201,true],[normal,null,201,false],[oversized,"image_dimensions",400,true],
      [tooLong,"jpeg_header_too_long",400,true],[tooLong,"jpeg_header_too_long",400,false],
    ] as const){
      data=bytes;read=0;
      vi.stubGlobal("fetch",vi.fn(async (target:string,init?:RequestInit)=>{
        if(target.includes("/search/issues"))return Response.json({items:[]});
        if(init?.method==="POST")return Response.json({...JSON.parse(String(init.body)),number:issue++,author_association:"OWNER"},{status:201});
        expect(new Headers(init?.headers).get("range")).toBe("bytes=0-"+(JPEG_HEADER_LIMIT-1));
        const limit=partial?Math.min(data.length,JPEG_HEADER_LIMIT):data.length;
        const body=new ReadableStream<Uint8Array>({pull(controller){
          if(read===limit){controller.close();return;}
          const end=Math.min(limit,read+4093);controller.enqueue(data.subarray(read,end));read=end;
        }});
        return new Response(body,{status:partial?206:200,headers:{
          "content-length":String(limit),
          ...(partial?{"content-range":"bytes 0-"+(limit-1)+"/"+data.length}:{}),
        }});
      }));
      const r=await worker.fetch(post("/v1/media",{...metadata,title:"JPEG "+status+" "+partial+" "+bytes.length,
        url:"https://cdn.example.com/phone.jpg"}),env);
      expect(r.status).toBe(status);
      if(code){
        const result=await r.json();
        expect(result).toMatchObject({error:{code}});
        if(code==="jpeg_header_too_long")expect(result).toMatchObject({error:{message:"Cabecera JPEG demasiado larga."}});
      }else{
        expect(await r.json()).toMatchObject({mime:"image/jpeg",bytes:data.length});
        if(bytes===phone)expect(read).toBeLessThan(500000);
      }
    }
  });
  it("uses the early VP8X canvas even with large WebP metadata and PNG IHDR",()=>{
    const b=new Uint8Array(PREFIX+65536),v=new DataView(b.buffer);
    b.set(new TextEncoder().encode("RIFF"),0);
    v.setUint32(4,b.length-8,true);
    b.set(new TextEncoder().encode("WEBPVP8X"),8);
    v.setUint32(16,10,true);b[20]=8;
    b.set([0x3f,0x10,0,0x2f,0x0c,0],24);
    b.set(new TextEncoder().encode("EXIF"),30);
    v.setUint32(34,b.length-38,true);
    expect(inspectHeader(b.subarray(0,PREFIX),"image")).toBe("image/webp");
    b.set([0xff,0xff,0xff],24);
    expect(()=>inspectHeader(b.subarray(0,PREFIX),"image")).toThrow("image_dimensions");
    expect(inspectHeader(png(4160,3120),"image")).toBe("image/png");
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
  it.each(["png","jpeg"] as const)("streams %s header and rest to GitHub, saves a manifest, and refuses a second write",async(format)=>{
    class TestFixedLengthStream extends TransformStream<Uint8Array,Uint8Array>{
      constructor(_size:number){super();}
    }
    vi.stubGlobal("FixedLengthStream",TestFixedLengthStream);
    const records=new Map<string,{name:string;size:number;state:string;browser_download_url:string}>();
    let manifest="", uploaded=0;
    const bytes=format==="png"?png(100,100,PREFIX+4096):jpegWithApps(phoneApps,4160,3120,524288);
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
          for(;;){const next=await reader.read();if(next.done)break;
            expect(next.value.every((value,i)=>value===bytes[size+i])).toBe(true);
            size+=next.value.length;chunks++;}
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
    expect(record).toMatchObject({media_id:t.media_id,bytes:bytes.length,origin:"upload",mime:format==="png"?"image/png":"image/jpeg"});
    expect(await (await worker.fetch(request(),env)).json()).toEqual(record);
    expect(uploaded).toBe(1);
    const bad=new Request(t.put_url,{method:"PUT",headers:{authorization:"Bearer "+t.upload_token+"x","content-length":"24"},body:png()});
    expect((await worker.fetch(bad,env)).status).toBe(401);
    expect(await (await worker.fetch(new Request(base+"/v1/media/"+t.media_id),env)).json()).toEqual(record);
  });
});

describe("queued mobile upload without a personal token",()=>{
  beforeEach(()=>{resetQuotaReservations();fakeCache();});
  afterEach(()=>vi.unstubAllGlobals());

  function fakeBindings(){
    const states=new Map<string,State>();
    const batches=new Map<string,BatchState>();
    const puts=vi.fn(async (_key:string,_value:Uint8Array,_options:unknown)=>{});
    const deletes=vi.fn(async (_key:string)=>{});
    const kv={put:puts,delete:deletes} as unknown as KVNamespace;
    const control={
      idFromName:(name:string)=>name,
      get:(id:string)=>({fetch:async (_url:string,init:RequestInit)=>{
        const command=JSON.parse(String(init.body));
        if(command.action.startsWith("session_")||command.action.startsWith("limit_")){
          const next=batchTransition(batches.get(id),command,Date.now());
          if(next.write&&next.state)batches.set(id,next.state);
          return Response.json(next.state??{},{status:next.code});
        }
        const next=transition(states.get(id),command,Date.now());
        if(next.write&&next.state)states.set(id,next.state);
        return Response.json(next.state??{},{status:next.code});
      }}),
    } as unknown as DurableObjectNamespace;
    return {states,puts,deletes,kv,control};
  }

  it("stores phone JPEG with large metadata and dispatches without a PAT",async()=>{
    const b=fakeBindings();
    const queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_ISSUE_TOKEN:"issue",
      CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const dispatch=vi.fn(async (_url:string,init?:RequestInit)=>{
      expect(new Headers(init?.headers).get("authorization")).toBe("Bearer dispatch");
      return new Response(null,{status:204});
    });
    vi.stubGlobal("fetch",dispatch);
    const created=await worker.fetch(post("/v1/media/upload",{...metadata,size:jpegWithApps(phoneApps).length}),queued);
    expect(created.status).toBe(201);
    const t=await created.json() as {media_id:string;put_url:string;upload_token:string;status:string};
    expect(t.status).toBe("uploading");
    const pending=await worker.fetch(new Request(base+"/v1/media/"+t.media_id),queued);
    expect(await pending.json()).toMatchObject({status:"uploading"});
    const bytes=jpegWithApps(phoneApps);
    const request=()=>new Request(t.put_url,{method:"PUT",headers:{
      authorization:"Bearer "+t.upload_token,"content-length":String(bytes.length)},body:bytes});
    const accepted=await worker.fetch(request(),queued);
    expect(accepted.status).toBe(202);
    expect(await accepted.json()).toEqual({media_id:t.media_id,status:"processing"});
    expect(b.puts).toHaveBeenCalledTimes(1);
    expect(b.puts.mock.calls[0]?.[2]).toEqual({expirationTtl:UPLOAD_TTL});
    expect(b.states.get(t.media_id)?.manifest?.mime).toBe("image/jpeg");
    const payload=JSON.parse(String(dispatch.mock.calls[0]?.[1]?.body));
    expect(payload.inputs.upload_id).toBe(t.media_id);
    expect(payload.inputs.capability).toMatch(/^[a-f0-9]{64}$/);
    expect(dispatch.mock.calls[0]?.[0]).toContain("/actions/workflows/media-ingest.yml/dispatches");
    expect((await worker.fetch(request(),queued)).status).toBe(409);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("rejects an oversized image without writing bytes or dispatching",async()=>{
    const b=fakeBindings();
    const queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",
      UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const dispatch=vi.fn();vi.stubGlobal("fetch",dispatch);
    const r=await worker.fetch(post("/v1/media/upload",{...metadata,size:11*1024*1024}),queued);
    expect(r.status).toBe(413);
    expect(b.states.size).toBe(0);
    expect(b.puts).not.toHaveBeenCalled();expect(dispatch).not.toHaveBeenCalled();
  });

  it("allows a public batch and public tickets without a write bearer",async()=>{
    const b=fakeBindings();
    const queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const sessionResponse=await worker.fetch(post("/v1/uploads",{},false),queued);
    expect(sessionResponse.status).toBe(201);
    const session=await sessionResponse.json() as {session_id:string;upload_url:string;max_files:number};
    expect(session.max_files).toBe(12);
    expect(session.upload_url).toContain("#"+session.session_id);
    const path="/v1/uploads/"+session.session_id;
    const ticket=await worker.fetch(post(path+"/files",{description:{es:"Lluvia"},kind:"image",size:24},false),queued);
    expect(ticket.status).toBe(201);
    const first=await ticket.json() as {media_id:string};
    const status=await worker.fetch(new Request(base+path),queued);
    expect(await status.json()).toMatchObject({files:[{media_id:first.media_id,kind:"image",status:"uploading"}]});
    expect((await worker.fetch(post("/v1/media/upload",{...metadata,size:24},false),queued)).status).toBe(201);
    const page=await worker.fetch(new Request(base+"/v1/uploads"),queued);
    const html=await page.text();
    expect(html).toContain("multiple");
    expect(html).toContain("formRows");
    expect(html).toContain("createElement(\"textarea\")");
    expect(html).toContain("Tags (opcional");
    expect(html).not.toContain("prompt(");
    expect((await worker.fetch(post(path+"/files",{kind:"image",size:24},false),queued)).status).toBe(400);
    for(let i=1;i<12;i++)expect((await worker.fetch(post(path+"/files",{...descriptive,kind:"image",size:24},false),queued)).status).toBe(201);
    expect((await worker.fetch(post(path+"/files",{...descriptive,kind:"image",size:24},false),queued)).status).toBe(409);
  });

  it("uploads parts out of order, replaces parts, validates sizes and completes once",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const dispatch=vi.fn(async()=>new Response(null,{status:204}));vi.stubGlobal("fetch",dispatch);
    const response=await worker.fetch(post("/v1/media/upload",{...metadata,kind:"video",size:CHUNK_BYTES+4},false),queued);
    expect(response.status).toBe(201);
    const t=await response.json() as {media_id:string;put_url:string;upload_token:string;part_size:number;parts:number;expires_at:string};
    expect(t.part_size).toBe(CHUNK_BYTES);expect(t.parts).toBe(2);
    expect(Date.parse(t.expires_at)-Date.now()).toBeGreaterThan(7190000);
    const part=(n:number,bytes:Uint8Array<ArrayBuffer>,declared=bytes.length)=>worker.fetch(new Request(t.put_url+"/parts/"+n,{
      method:"PUT",headers:{authorization:"Bearer "+t.upload_token,"content-length":String(declared)},body:bytes}),queued);
    const complete=()=>worker.fetch(new Request(t.put_url+"/complete",{method:"POST",headers:{authorization:"Bearer "+t.upload_token}}),queued);
    expect((await complete()).status).toBe(409);
    expect((await part(1,new Uint8Array(3))).status).toBe(400);
    expect((await part(1,new Uint8Array(3),4)).status).toBe(400);
    expect((await part(2,new Uint8Array(4))).status).toBe(400);
    expect((await part(1,new Uint8Array([1,2,3,4]))).status).toBe(200);
    expect((await complete()).status).toBe(409);
    const retry=await part(1,new Uint8Array([1,2,3,4]));expect(retry.status).toBe(200);
    expect(b.puts).toHaveBeenCalledTimes(1);
    const replaced=await part(1,new Uint8Array([4,3,2,1]));expect(replaced.status).toBe(200);
    const receipt=await replaced.json() as {sha256:string};
    const head=new Uint8Array(CHUNK_BYTES);head.set(mp4(10));
    expect((await part(0,head)).status).toBe(200);
    expect((await complete()).status).toBe(202);
    expect(b.states.get(t.media_id)?.manifest?.chunks[1]?.sha256).toBe(receipt.sha256);
    expect(b.states.get(t.media_id)?.manifest?.chunks.map(c=>c.bytes)).toEqual([CHUNK_BYTES,4]);
    expect((await complete()).status).toBe(409);
    expect((await part(1,new Uint8Array(4))).status).toBe(409);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("rejects expired tickets and directs large single PUTs to parts",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const dispatch=vi.fn();vi.stubGlobal("fetch",dispatch);
    const size=100*1024*1024;
    expect((await worker.fetch(post("/v1/media/upload",{...metadata,kind:"video"},false),queued)).status).toBe(400);
    const t=await (await worker.fetch(post("/v1/media/upload",{...metadata,kind:"video",size},false),queued)).json() as {media_id:string;put_url:string;upload_token:string;parts:number};
    expect(t.parts).toBe(5);
    const put=await worker.fetch(new Request(t.put_url,{method:"PUT",headers:{authorization:"Bearer "+t.upload_token,"content-length":String(size)},body:mp4(1)}),queued);
    expect(put.status).toBe(413);expect(await put.json()).toMatchObject({error:{code:"use_parts"}});
    expect(b.states.get(t.media_id)?.status).toBe("uploading");
    const clock=vi.spyOn(Date,"now").mockReturnValue(Date.now()+7200001);
    try{
      expect((await worker.fetch(new Request(t.put_url+"/parts/4",{method:"PUT",headers:{authorization:"Bearer "+t.upload_token,"content-length":String(4*1024*1024)},body:mp4(1)}),queued)).status).toBe(401);
      expect((await worker.fetch(new Request(t.put_url+"/complete",{method:"POST",headers:{authorization:"Bearer "+t.upload_token}}),queued)).status).toBe(401);
    }finally{clock.mockRestore();}
    expect(b.puts).not.toHaveBeenCalled();expect(dispatch).not.toHaveBeenCalled();
  });

  it("stages small base64 images and rejects untrusted file URLs without fetching",async()=>{
    const b=fakeBindings();
    const queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const path="/v1/uploads/"+session.session_id+"/files";
    const upstream=vi.fn(async ()=>new Response(null,{status:204}));
    vi.stubGlobal("fetch",upstream);
    const rejected=await worker.fetch(post(path,{file:{file_id:"file_1",download_url:"https://oaiusercontent.com.evil.example/x"}},false),queued);
    expect(rejected.status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
    const image_base64=btoa(String.fromCharCode(...png()));
    const accepted=await worker.fetch(post(path,{...descriptive,image_base64},false),queued);
    expect(accepted.status).toBe(202);
    expect(await accepted.json()).toMatchObject({status:"processing"});
    expect(b.puts).toHaveBeenCalledTimes(1);
  });

  it("downloads a permitted temporary file without forwarding authorization or signed URL",async()=>{
    const b=fakeBindings();
    const queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const remote="https://files.oaiusercontent.com/x?signature=temporary";
    const fetcher=vi.fn(async (url:string,init?:RequestInit)=>{
      if(url===remote){
        expect(init?.redirect).toBe("manual");
        expect(new Headers(init?.headers).get("authorization")).toBeNull();
        return new Response(png(),{headers:{"content-length":"24","content-type":"image/png"}});
      }
      expect(url).toContain("/dispatches");
      expect(String(init?.body)).not.toContain("signature");
      return new Response(null,{status:204});
    });
    vi.stubGlobal("fetch",fetcher);
    const response=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,file:{file_id:"file_1",download_url:remote}},false),queued);
    expect(response.status).toBe(202);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("follows at most three trusted redirects, never sending credentials",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const urls=["https://files.oaiusercontent.com/a?token=secret","https://files.openai.com/b?token=secret",
      "https://cdn.oaiusercontent.com/c?token=secret","https://cdn.oaiusercontent.com/d"];
    const fetcher=vi.fn(async(url:string,init?:RequestInit)=>{
      const i=urls.indexOf(url);
      if(i>=0){
        expect(init?.redirect).toBe("manual");
        expect(new Headers(init?.headers).get("authorization")).toBeNull();
        return i===3?new Response(png(),{headers:{"content-type":"image/png"}}):
          new Response(null,{status:302,headers:{location:urls[i+1]!}});
      }
      expect(url).toContain("/dispatches");expect(String(init?.body)).not.toContain("secret");
      return new Response(null,{status:204});
    });
    vi.stubGlobal("fetch",fetcher);
    const r=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,file:{file_id:"file_1",download_url:urls[0]}},false),queued);
    expect(r.status).toBe(202);
    const value=await r.json() as {media_id:string};
    expect(b.states.get(value.media_id)?.manifest).toMatchObject({bytes:24,mime:"image/png"});
    expect(fetcher).toHaveBeenCalledTimes(5);
  });

  it.each(["https://evil.example/secret?token=secret","http://files.oaiusercontent.com/secret?token=secret",
    "https://user:password@files.oaiusercontent.com/secret?token=secret"])("blocks unsafe redirect %s",async(destination)=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const fetcher=vi.fn(async()=>new Response(null,{status:302,headers:{location:destination}}));
    vi.stubGlobal("fetch",fetcher);
    const r=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,kind:"image",file:{file_id:"file_1",download_url:"https://files.oaiusercontent.com/start"}},false),queued);
    expect(r.status).toBe(400);
    const result=await r.json() as {error:{code:string;message:string}};
    expect(result.error.code).toBe("redirect");
    expect(result.error.message).toContain(new URL(destination).hostname);
    expect(result.error.message).not.toMatch(/secret|password|token=/);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(b.states.size).toBe(0);
  });

  it("stops redirect loops and reports safe origin errors before making a ticket",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const path="/v1/uploads/"+session.session_id+"/files",url="https://files.oaiusercontent.com/start?token=secret";
    const run=(file:unknown)=>worker.fetch(post(path,{...descriptive,kind:"image",file},false),queued);
    const fetcher=vi.fn(async()=>new Response(null,{status:302,headers:{location:"/again?token=secret"}}));
    vi.stubGlobal("fetch",fetcher);
    const loop=await run({file_id:"file_1",download_url:url});
    expect(await loop.json()).toMatchObject({error:{code:"redirect",message:expect.stringContaining("Maximo 3")}});
    expect(fetcher).toHaveBeenCalledTimes(4);
    const invalid=await run({file_id:"file_1",download_url:"https://evil.example/private?token=secret"});
    const info=await invalid.json() as {error:{code:string;message:string}};
    expect(info.error).toMatchObject({code:"invalid_url",message:expect.stringContaining("evil.example")});
    expect(info.error.message).not.toMatch(/private|secret/);
    for(const file of ["file_1",{file_id:"file_1"}]){
      expect(await (await run(file)).json()).toMatchObject({error:{code:"missing_download_url"}});
    }
    expect(fetcher).toHaveBeenCalledTimes(4);
    fetcher.mockImplementation(async()=>new Response("secret",{status:403}));
    expect(await (await run({file_id:"file_1",download_url:url})).json()).toMatchObject({error:{code:"source_status_403",message:expect.stringContaining("HTTP 403")}});
    fetcher.mockImplementation(async()=>new Response(png(),{headers:{"content-encoding":"gzip"}}));
    expect(await (await run({file_id:"file_1",download_url:url})).json()).toMatchObject({error:{code:"invalid_encoding"}});
    fetcher.mockImplementation(async()=>new Response(png(),{headers:{"content-length":"0"}}));
    expect(await (await run({file_id:"file_1",download_url:url})).json()).toMatchObject({error:{code:"invalid_length",message:expect.stringContaining("Content-Length")}});
    expect(b.states.size).toBe(0);
  });

  it.each(["image","video"] as const)("streams a short %s without Content-Length",async(kind)=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const url="https://files.oaiusercontent.com/asset";
    const bytes=kind==="image"?png():mp4(10);
    vi.stubGlobal("fetch",vi.fn(async(u:string)=>u===url?new Response(bytes):new Response(null,{status:204})));
    const r=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,kind,file:{file_id:"file_1",download_url:url}},false),queued);
    expect(r.status).toBe(202);
    const value=await r.json() as {media_id:string};
    expect(b.states.get(value.media_id)?.manifest).toMatchObject({bytes:bytes.length,mime:kind==="image"?"image/png":"video/mp4"});
  });

  it("aborts an oversized unknown-length stream and never dispatches",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const block=new Uint8Array(1024*1024);block.set(png());
    let remaining=11;
    const stream=new ReadableStream<Uint8Array>({pull(c){if(remaining--<=0)c.close();else c.enqueue(block);}});
    const fetcher=vi.fn(async()=>new Response(stream));
    vi.stubGlobal("fetch",fetcher);
    const r=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,kind:"image",file:{file_id:"file_1",download_url:"https://files.oaiusercontent.com/asset"}},false),queued);
    expect(r.status).toBe(413);expect(await r.json()).toMatchObject({error:{code:"media_too_large"}});
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect([...b.states.values()][0]?.status).toBe("rejected");
    expect(b.deletes).toHaveBeenCalled();
  });

  it("stages a 96 MiB remote video internally in 24 MiB chunks",async()=>{
    const b=fakeBindings(),queued={CLIPOTECA_WRITE_TOKEN:token,CLIPOTECA_DISPATCH_TOKEN:"dispatch",UPLOADS:b.kv,UPLOAD_CONTROL:b.control};
    const session=await (await worker.fetch(post("/v1/uploads",{},false),queued)).json() as {session_id:string};
    const block=new Uint8Array(1024*1024);block.set(mp4(10));
    let remaining=96;
    const stream=new ReadableStream<Uint8Array>({pull(c){if(remaining--<=0)c.close();else c.enqueue(block);}});
    const url="https://files.oaiusercontent.com/asset";
    const fetcher=vi.fn(async(u:string)=>u===url?new Response(stream,{headers:{"content-length":String(96*block.length)}}):new Response(null,{status:204}));
    vi.stubGlobal("fetch",fetcher);
    const r=await worker.fetch(post("/v1/uploads/"+session.session_id+"/files",{...descriptive,kind:"video",file:{file_id:"file_1",download_url:url}},false),queued);
    expect(r.status).toBe(202);
    const result=await r.json() as {media_id:string};
    expect(b.states.get(result.media_id)?.manifest?.chunks.map(c=>c.bytes)).toEqual(Array(4).fill(CHUNK_BYTES));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("chunks 100 MiB into bounded KV values, each with a two hour TTL",async()=>{
    const b=fakeBindings(),block=new Uint8Array(1024*1024);
    let remaining=100;
    const stream=new ReadableStream<Uint8Array>({pull(c){
      if(remaining--<=0)c.close();else c.enqueue(block);
    }});
    const result=await stage(b.kv,"a2026-11-"+"a".repeat(32),100*block.length,stream.getReader(),[]);
    expect(result.map(c=>c.bytes)).toEqual([CHUNK_BYTES,CHUNK_BYTES,CHUNK_BYTES,CHUNK_BYTES,4*block.length]);
    expect(result.every(c=>/^[a-f0-9]{64}$/.test(c.sha256))).toBe(true);
    expect(b.puts.mock.calls.map(c=>c[1].length)).toEqual([CHUNK_BYTES,CHUNK_BYTES,CHUNK_BYTES,CHUNK_BYTES,4*block.length]);
    expect(b.puts.mock.calls.every(c=>JSON.stringify(c[2])===JSON.stringify({expirationTtl:7200}))).toBe(true);
  });

  it("consumes a capability for one run, permits its retries and expires it",()=>{
    const now=100,hash="a".repeat(64);
    const manifest:Manifest={kind:"image",title:"T",author:"A",source:"S",media_id:"a2026-11-"+"a".repeat(32),
      mime:"image/png",bytes:24,created_at:new Date().toISOString(),chunks:[{bytes:24,sha256:hash}]};
    const created=transition(undefined,{action:"create",expires:now+1000},now).state!;
    const begun=transition(created,{action:"begin"},now).state!;
    expect(transition(begun,{action:"begin"},now).code).toBe(409);
    const ready=transition(begun,{action:"ready",manifest,capabilityHash:hash},now).state!;
    const claim={action:"claim",run:"1:1",capabilityHash:hash};
    const claimed=transition(ready,claim,now).state!;
    expect(transition(claimed,claim,now).code).toBe(200);
    expect(transition(claimed,{...claim,run:"2:1"},now).code).toBe(409);
    expect(transition(claimed,{...claim,action:"read",run:"2:1"},now).code).toBe(401);
    expect(transition(claimed,{...claim,action:"read"},now).code).toBe(200);
    const done=transition(claimed,{...claim,action:"finish",status:"registered"},now).state!;
    expect(done.status).toBe("registered");
    expect(transition(done,{...claim,action:"read"},now).code).toBe(409);
    expect(transition(ready,claim,now+31*60*1000).code).toBe(401);
  });

  it("does not accept a public capability as Actions authentication",async()=>{
    const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
    expect(await actionsRun(new Request(base,{headers:{"x-clipoteca-capability":"a".repeat(64)}}),{})).toBeNull();
    expect(await actionsRun(new Request(base,{headers:{authorization:"Bearer "+"a".repeat(64)}}),{})).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("durable batch admission",()=>{
  it("limits sessions per hour, files per session, and bytes across IPs",()=>{
    const now=Date.parse("2026-11-25T12:00:00Z"),ip="a".repeat(64);
    let state:BatchState|undefined;
    for(let i=0;i<4;i++){
      const result=batchTransition(state,{action:"limit_session",ip},now);
      expect(result.code).toBe(200);state=result.state;
    }
    expect(batchTransition(state,{action:"limit_session",ip},now).code).toBe(429);
    expect(batchTransition(state,{action:"limit_session",ip},now+3600000).code).toBe(200);
    let session=batchTransition(undefined,{action:"session_create"},now).state;
    for(let i=0;i<12;i++){
      const result=batchTransition(session,{action:"session_add",file:{media_id:"a2026-11-"+i.toString(16).padStart(32,"0"),kind:"image"}},now);
      expect(result.code).toBe(200);session=result.state;
    }
    expect(batchTransition(session,{action:"session_add",file:{media_id:"a2026-11-"+"f".repeat(32),kind:"image"}},now).code).toBe(409);
    expect(batchTransition(session,{action:"session_add"},now+86400000).code).toBe(410);
    state=undefined;
    for(let i=0;i<40;i++){
      const result=batchTransition(state,{action:"limit_bytes",ip,bytes:1},now);
      expect(result.code).toBe(200);state=result.state;
    }
    expect(batchTransition(state,{action:"limit_bytes",ip,bytes:1},now).code).toBe(429);
    state=undefined;
    for(let i=0;i<10;i++){
      const result=batchTransition(state,{action:"limit_bytes",ip,bytes:100*1024*1024},now);
      expect(result.code).toBe(200);state=result.state;
    }
    expect(batchTransition(state,{action:"limit_bytes",ip,bytes:25*1024*1024},now).code).toBe(429);
    expect(batchTransition(state,{action:"limit_bytes",ip,bytes:24*1024*1024},now).code).toBe(200);
    state=undefined;
    for(let i=0;i<20;i++){
      const result=batchTransition(state,{action:"limit_bytes",ip:i.toString(16).padStart(64,"0"),bytes:100*1024*1024},now);
      expect(result.code).toBe(200);state=result.state;
    }
    expect(batchTransition(state,{action:"limit_bytes",ip:"f".repeat(64),bytes:100*1024*1024},now).code).toBe(429);
  });
});

describe("multipart reservation expiry",()=>{
  it("allows takeover after two minutes and fences late completion and abort",()=>{
    const now=100, first="a".repeat(64), second="b".repeat(64);
    const chunk={bytes:24,sha256:"c".repeat(64)};
    const old=transition(undefined,{action:"create",expires:now+7200000,size:24},now).state!;
    const begin={action:"part_begin",index:0,chunk,attempt:first};
    const locked=transition(old,begin,now).state!;
    expect(transition(locked,{...begin,attempt:second},now+119999).code).toBe(409);
    const replacement=transition(locked,{...begin,attempt:second},now+120000).state!;
    expect(transition(replacement,{...begin,action:"part_done",mime:"image/png"},now+120001).code).toBe(409);
    expect(transition(replacement,{...begin,action:"part_abort"},now+120001).code).toBe(409);
    const done=transition(replacement,{...begin,attempt:second,action:"part_done",mime:"image/png"},now+120001);
    expect(done.code).toBe(200);
    expect(done.state?.parts?.[0]).toEqual({...chunk,keyed:true});
  });
});

describe("searchable contribution registry",()=>{
  it("finds QA-registered bilingual uploads by tags and description",async()=>{
    const upload={...metadata,media_id:"a2026-11-"+"a".repeat(32),status:"registered"};
    vi.stubGlobal("fetch",vi.fn(async()=>Response.json([
      upload,{...upload,media_id:"a2026-11-"+"b".repeat(32),status:"processing"},
    ])));
    const result=await worker.fetch(new Request(base+"/v1/media/search?q=rain"),{});
    expect(await result.json()).toEqual({items:[upload]});
    const none=await worker.fetch(new Request(base+"/v1/media/search?q=snow"),{});
    expect(await none.json()).toEqual({items:[]});
  });
});

describe("public render global cap", () => {
  it("allows renders up to GLOBAL_VIDEOS per UTC day and then rejects", async () => {
    const {batchTransition, GLOBAL_VIDEOS} = await import("../src/upload-batch-control");
    const ip = "a".repeat(64);
    let state;
    for (let i = 0; i < GLOBAL_VIDEOS; i++) {
      const next = batchTransition(state, {action: "limit_video", ip}, Date.now());
      expect(next.code).toBe(200);
      state = next.state;
    }
    expect(batchTransition(state, {action: "limit_video", ip}, Date.now()).code).toBe(429);
  });
});
