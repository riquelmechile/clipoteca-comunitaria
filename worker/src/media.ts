/** Public contribution registry and single-use Release upload capability. */
import {inspectHeader, maxBytes, mediaUrl, PREFIX, videoDuration, type MediaKind} from "./media-header";
import {quota} from "./quota";

export interface MediaEnv {
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
  CLIPOTECA_UPLOAD_TOKEN?: string;
}
export const PUBLIC_NOTICE = "Todo lo que subas (archivos, guion y video final) queda p\u00fablico.";
type Metadata = {kind: MediaKind; title: string; author: string; source: string; license_note?: string};
type RecordMedia = Metadata & {
  media_id: string; bytes: number; mime: string; origin: "url" | "upload";
  url: string; created_at: string; status: "registered"; duration_seconds?: number;
};
type Ticket = Metadata & {id:string; exp:number};
type Asset = {name:string; size:number; state:string; browser_download_url:string};
type Release = {id:number; assets:Asset[]};
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const json = (value:unknown,status=200) => Response.json(value,{status,headers:{"cache-control":"no-store","referrer-policy":"no-referrer"}});
const err = (code:string,status:number) => json({error:{code,message:code}},status);
const hex = (b:Uint8Array) => Array.from(b,x=>x.toString(16).padStart(2,"0")).join("");
const digest = async (s:string) => hex(new Uint8Array(await crypto.subtle.digest("SHA-256",encoder.encode(s))));
const repo = (env:MediaEnv) => {
  const value=env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if(!/^[\w.-]+\/[\w.-]+$/.test(value))throw new Error("invalid_repository");
  return value;
};
const api = (env:MediaEnv,path:string,token?:string,init:RequestInit={}) =>
  fetch("https://api.github.com/repos/"+repo(env)+path,{
    ...init,redirect:"manual",headers:{
      accept:"application/vnd.github+json","user-agent":"clipoteca-worker",
      "x-github-api-version":"2022-11-28",
      ...(token?{authorization:"Bearer "+token}:{}),
      ...(init.body?{"content-type":"application/json"}:{}),
    },
  });
const authorized = (request:Request,env:MediaEnv) =>
  Boolean(env.CLIPOTECA_WRITE_TOKEN && env.CLIPOTECA_WRITE_TOKEN.length>=16 &&
    request.headers.get("authorization")==="Bearer "+env.CLIPOTECA_WRITE_TOKEN);
const object = (x:unknown):x is Record<string,unknown> => Boolean(x && typeof x==="object" && !Array.isArray(x));
function metadata(x:unknown,extra:string[]=[]):Metadata {
  if(!object(x) || Object.keys(x).some(k=>!["kind","title","author","source","license_note",...extra].includes(k)) ||
    (x.kind!=="video" && x.kind!=="image"))throw new TypeError("invalid_media");
  const field=(name:string,max:number)=>{
    const value=x[name];
    if(typeof value!=="string" || !value.trim() || value.length>max || /[\x00-\x1f\x7f]/.test(value))
      throw new TypeError("invalid_"+name);
    return value.trim();
  };
  return {kind:x.kind,title:field("title",120),author:field("author",120),source:field("source",500),
    ...(x.license_note===undefined?{}:{license_note:field("license_note",500)})};
}
async function bodyJson(request:Request):Promise<unknown> {
  if(!request.headers.get("content-type")?.includes("application/json") || !request.body)throw new TypeError("invalid_json");
  const reader=request.body.getReader();
  const localDecoder = new TextDecoder("utf-8",{fatal:true});
  let size=0,raw="";
  try {
    while(true){
      const part=await reader.read();
      if(part.done)break;
      size+=part.value.length;
      if(size>8192)throw new TypeError("invalid_json");
      raw+=localDecoder.decode(part.value,{stream:true});
    }
    raw+=localDecoder.decode();
    return JSON.parse(raw);
  }finally{await reader.cancel().catch(()=>{});}
}
function length(value:string|null):number {
  if(!value || !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))throw new TypeError("invalid_length");
  return Number(value);
}
/** Read only a bounded prefix and preserve unread bytes of the current chunk. */
async function prefix(reader:ReadableStreamDefaultReader<Uint8Array>,limit:number) {
  const buffer=new Uint8Array(limit);
  let n=0,tail:Uint8Array|undefined;
  while(n<limit){
    const part=await reader.read();
    if(part.done)break;
    const count=Math.min(limit-n,part.value.length);
    buffer.set(part.value.subarray(0,count),n);n+=count;
    if(count<part.value.length){tail=part.value.subarray(count);break;}
  }
  return {head:buffer.subarray(0,n),tail};
}
function checked(head:Uint8Array,kind:MediaKind) {
  const mime=inspectHeader(head,kind);
  if(kind==="image")return {mime};
  const duration=videoDuration(head,mime);
  if(duration===null || duration>120)throw new TypeError("video_duration_unknown_or_exceeds_120s");
  return {mime,duration_seconds:duration};
}
async function probe(url:string,kind:MediaKind) {
  const response=await fetch(url,{redirect:"manual",headers:{range:"bytes=0-"+(PREFIX-1),"accept-encoding":"identity"}});
  if(!response.body)throw new TypeError("invalid_source");
  const reader=response.body.getReader();
  try{
    if(response.headers.get("content-encoding") && response.headers.get("content-encoding")!=="identity")
      throw new TypeError("invalid_encoding");
    let bytes:number,expected:number;
    if(response.status===206){
      const range=/^bytes 0-(\d+)\/([1-9]\d*)$/.exec(response.headers.get("content-range")??"");
      if(!range)throw new TypeError("invalid_range");
      bytes=length(range[2]!);expected=Math.min(bytes,PREFIX);
      if(Number(range[1])+1!==expected || length(response.headers.get("content-length"))!==expected)
        throw new TypeError("invalid_range");
    }else if(response.status===200){
      bytes=length(response.headers.get("content-length"));expected=Math.min(bytes,PREFIX);
    }else throw new TypeError("invalid_source");
    if(bytes>maxBytes(kind))throw new TypeError("media_too_large");
    const {head}=await prefix(reader,expected);
    if(head.length!==expected)throw new TypeError("truncated_source");
    return {bytes,...checked(head,kind)};
  }finally{await reader.cancel().catch(()=>{});}
}
// URL registration uses the existing Issues grant: no contents:write is needed.
// Issue body is a public JSON manifest, addressed directly by its issue number.
const marker="clipoteca-media-v1";
function parseIssue(row:unknown):RecordMedia|null {
  if(!object(row) || typeof row.body!=="string" || typeof row.title!=="string" ||
    !/^\[media-v1\] [a-f0-9]{64}$/.test(row.title) || row.pull_request || !Number.isSafeInteger(row.number) ||
    !["OWNER","MEMBER","COLLABORATOR"].includes(String(row.author_association)))return null;
  try{
    const r=JSON.parse(row.body) as RecordMedia;
    if(!object(r) || (r as unknown as {registry:string}).registry!==marker || r.origin!=="url")return null;
    const {registry:_registry,...record}=r as RecordMedia & {registry:string};
    return {...record,media_id:"u"+Number(row.number).toString(16)};
  }catch{return null;}
}
const reservations=new Set<string>();
async function registerUrl(request:Request,env:MediaEnv) {
  if(!env.CLIPOTECA_ISSUE_TOKEN)return err("media_registry_not_configured",503);
  const raw=await bodyJson(request);
  const meta=metadata(raw,["url"]);
  if(!object(raw)||typeof raw.url!=="string")throw new TypeError("invalid_url");
  const url=mediaUrl(raw.url,meta.kind);
  const key=await digest(JSON.stringify({...meta,url}));
  if(reservations.has(key))return err("media_registration_in_progress",409);
  reservations.add(key);
  try{
    const cache=(caches as CacheStorage & {default:Cache}).default;
    const ck=new Request("https://clipoteca.invalid/_media_registered/"+key);
    const cached=await cache?.match(ck);
    if(cached)return json(await cached.json());
    // Search handles retries after cache eviction; GitHub search is eventually consistent.
    const query=encodeURIComponent("repo:"+repo(env)+" is:issue in:title "+key);
    const found=await fetch("https://api.github.com/search/issues?q="+query,{
      headers:{accept:"application/vnd.github+json",authorization:"Bearer "+env.CLIPOTECA_ISSUE_TOKEN,
        "user-agent":"clipoteca-worker"},redirect:"manual",
    });
    if(!found.ok)return err("media_registry_unavailable",503);
    const rows=(await found.json() as {items:unknown[]}).items;
    if(!Array.isArray(rows))return err("media_registry_unavailable",503);
    for(const row of rows){
      if(object(row)&&row.title==="[media-v1] "+key){
        const record=parseIssue(row);
        if(record)return json(record);
      }
    }
    const info=await probe(url,meta.kind);
    if(!await quota(request,"media",info.bytes))return err("rate_limited",429);
    const manifest={...meta,...info,origin:"url",url,created_at:new Date().toISOString(),status:"registered",registry:marker};
    const response=await api(env,"/issues",env.CLIPOTECA_ISSUE_TOKEN,{method:"POST",
      body:JSON.stringify({title:"[media-v1] "+key,body:JSON.stringify(manifest)})});
    if(response.status!==201)return err("media_registry_unavailable",503);
    const record=parseIssue(await response.json());
    if(!record)return err("media_registry_unavailable",503);
    await cache?.put(ck,new Response(JSON.stringify(record),{headers:{"cache-control":"public,max-age=86400"}})).catch(()=>{});
    return json(record,201);
  }finally{reservations.delete(key);}
}
const b64=(bytes:Uint8Array)=>btoa(String.fromCharCode(...bytes)).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
const unb64=(s:string)=>Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/")),c=>c.charCodeAt(0));
const signKey=(env:MediaEnv)=>crypto.subtle.importKey("raw",encoder.encode(env.CLIPOTECA_WRITE_TOKEN??""),
  {name:"HMAC",hash:"SHA-256"},false,["sign","verify"]);
const UPLOAD_ID=/^a\d{4}-\d{2}-[a-f0-9]{32}$/;
async function ticket(request:Request,env:MediaEnv) {
  const meta=metadata(await bodyJson(request));
  if(!await quota(request,"media-ticket"))return err("rate_limited",429);
  const id="a"+new Date().toISOString().slice(0,7)+"-"+hex(crypto.getRandomValues(new Uint8Array(16)));
  const exp=Date.now()+15*60*1000;
  const payload=b64(encoder.encode(JSON.stringify({...meta,id,exp})));
  const sig=b64(new Uint8Array(await crypto.subtle.sign("HMAC",await signKey(env),encoder.encode(payload))));
  const capability=payload+"."+sig;
  return json({media_id:id,status:"pending_upload",expires_at:new Date(exp).toISOString(),
    upload_url:new URL("/v1/media/upload",request.url).href+"#"+capability,
    put_url:new URL("/v1/media/upload/"+id,request.url).href,upload_token:capability},201);
}
async function verify(request:Request,env:MediaEnv,id:string):Promise<Ticket|null>{
  const token=request.headers.get("authorization")?.slice(7)??"";
  if(!request.headers.get("authorization")?.startsWith("Bearer ") || token.length>8192 ||
    !env.CLIPOTECA_WRITE_TOKEN || env.CLIPOTECA_WRITE_TOKEN.length<16)return null;
  const [payload,sig,...rest]=token.split(".");
  if(!payload||!sig||rest.length)return null;
  try{
    if(!await crypto.subtle.verify("HMAC",await signKey(env),unb64(sig),encoder.encode(payload)))return null;
    const value=JSON.parse(decoder.decode(unb64(payload))) as Ticket;
    if(value.id!==id || !Number.isSafeInteger(value.exp) || value.exp<=Date.now())return null;
    metadata(value,["id","exp"]);return value;
  }catch{return null;}
}
const tag=(id:string)=>"uploads-"+id.slice(1,8);
async function release(env:MediaEnv,id:string,create=false):Promise<Release|null>{
  const token=create?env.CLIPOTECA_UPLOAD_TOKEN:undefined;
  let response=await api(env,"/releases/tags/"+tag(id),token);
  if(response.status===404 && create){
    response=await api(env,"/releases",token,{method:"POST",body:JSON.stringify({
      tag_name:tag(id),name:"Aportes "+id.slice(1,8),target_commitish:"main",prerelease:true,draft:false,
    })});
    if(response.status===422)response=await api(env,"/releases/tags/"+tag(id),token);
  }
  if(response.status===404)return null;
  if(!response.ok)throw new Error("release_unavailable");
  const row=await response.json() as Release;
  if(!Number.isSafeInteger(row.id))throw new Error("release_unavailable");
  return row;
}
async function asset(env:MediaEnv,r:Release,name:string):Promise<Asset|undefined>{
  // The release payload can truncate assets. Page explicitly.
  for(let page=1;page<=20;page++){
    const response=await api(env,"/releases/"+r.id+"/assets?per_page=100&page="+page,env.CLIPOTECA_UPLOAD_TOKEN);
    if(!response.ok)throw new Error("release_unavailable");
    const rows=await response.json() as Asset[];
    if(!Array.isArray(rows))throw new Error("release_unavailable");
    const found=rows.find(a=>a.name===name);
    if(found)return found;
    if(rows.length<100)return undefined;
  }
  throw new Error("release_asset_limit");
}
function putAsset(env:MediaEnv,r:Release,name:string,mime:string,bytes:number,body:BodyInit){
  return fetch("https://uploads.github.com/repos/"+repo(env)+"/releases/"+r.id+"/assets?name="+encodeURIComponent(name),{
    method:"POST",redirect:"manual",headers:{authorization:"Bearer "+env.CLIPOTECA_UPLOAD_TOKEN,
      "content-type":mime,"content-length":String(bytes),"user-agent":"clipoteca-worker",
      accept:"application/vnd.github+json"},body,
  });
}
async function getUpload(env:MediaEnv,id:string):Promise<RecordMedia|null>{
  const r=await release(env,id);
  if(!r)return null;
  const found=await asset(env,r,id+".json");
  if(!found || found.state!=="uploaded")return null;
  // Construct the trusted public Release URL ourselves; no token on redirects.
  const response=await fetch("https://github.com/"+repo(env)+"/releases/download/"+tag(id)+"/"+id+".json");
  if(!response.ok)throw new Error("media_registry_unavailable");
  const value=await response.json() as RecordMedia;
  if(value.media_id!==id || value.origin!=="upload")throw new Error("invalid_manifest");
  return value;
}
async function upload(request:Request,env:MediaEnv,id:string){
  const claim=await verify(request,env,id);
  if(!claim)return err("invalid_or_expired_upload",401);
  if(reservations.has(id))return err("upload_in_progress",409);
  reservations.add(id);
  let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
  try{
    const prior=await getUpload(env,id);
    if(prior)return json(prior); // Idempotent receipt; never write a second asset.
    const bytes=length(request.headers.get("content-length"));
    if(bytes>maxBytes(claim.kind))return err("media_too_large",413);
    if(!request.body)return err("invalid_media",400);
    reader=request.body.getReader();
    const {head,tail}=await prefix(reader,Math.min(PREFIX,bytes));
    if(head.length!==Math.min(PREFIX,bytes))return err("truncated_upload",400);
    const info=checked(head,claim.kind);
    if(!await quota(request,"media",bytes))return err("rate_limited",429);
    const r=await release(env,id,true);
    if(!r)return err("release_unavailable",503);
    const name=id+".media";
    if(await asset(env,r,name))return err("upload_already_used",409);
    // FixedLengthStream gives Cloudflare a real Content-Length; no full-file buffer.
    const stream=new FixedLengthStream(bytes);
    const writer=stream.writable.getWriter();
    const activeReader=reader;
    const pump=async()=>{
      let sent=0;
      const write=async (chunk:Uint8Array)=>{
        sent+=chunk.length;
        if(sent>bytes)throw new Error("upload_length_mismatch");
        await writer.write(chunk);
      };
      try{
        await write(head);
        if(tail)await write(tail);
        while(true){
          const part=await activeReader.read();
          if(part.done)break;
          await write(part.value);
        }
        if(sent!==bytes)throw new Error("upload_length_mismatch");
        await writer.close();
      }catch(e){await writer.abort(e).catch(()=>{});throw e;}
    };
    const [result]=await Promise.all([putAsset(env,r,name,info.mime,bytes,stream.readable),pump()]);
    if(result.status!==201)return err(result.status===422?"upload_already_used":"upload_unavailable",result.status===422?409:503);
    const receipt=await result.json() as Asset;
    if(receipt.state!=="uploaded" || receipt.size!==bytes || receipt.name!==name)return err("upload_unavailable",503);
    const {id:_id,exp:_exp,...meta}=claim;
    const record:RecordMedia={...meta,...info,media_id:id,bytes,origin:"upload",
      url:"https://github.com/"+repo(env)+"/releases/download/"+tag(id)+"/"+name,
      created_at:new Date().toISOString(),status:"registered"};
    const manifest=JSON.stringify(record);
    const saved=await putAsset(env,r,id+".json","application/json",encoder.encode(manifest).length,manifest);
    if(saved.status!==201)return err("upload_manifest_unavailable",503);
    return json(record,201);
  }finally{
    reservations.delete(id);
    await reader?.cancel().catch(()=>{});
  }
}
function page(configured:boolean):Response {
  const nonce=hex(crypto.getRandomValues(new Uint8Array(16)));
  const html='<!doctype html><html lang="es"><meta charset="utf-8"><title>Aporte a Clipoteca</title>'+
    '<meta name="viewport" content="width=device-width,initial-scale=1"><h1>Aporte a Clipoteca</h1><p>'+PUBLIC_NOTICE+'</p>'+
    (configured?'<p>Video: 50 MiB y 120 s. Imagen: 10 MiB y 24 MP.</p><input id="file" type="file" accept="video/mp4,video/webm,video/quicktime,image/jpeg,image/png,image/webp"><button id="send">Subir aporte p\u00fablico</button><pre id="result"></pre>'+
    '<script nonce="'+nonce+'">const t=location.hash.slice(1);history.replaceState(null,"",location.pathname);document.getElementById("send").onclick=async()=>{const o=document.getElementById("result"),f=document.getElementById("file").files[0];try{if(!f||!t)throw Error("Falta archivo o enlace");const p=t.split(".")[0].replace(/-/g,"+").replace(/_/g,"/");const id=JSON.parse(atob(p)).id;if(!/^a\\d{4}-\\d{2}-[a-f0-9]{32}$/.test(id))throw Error("Enlace inv\u00e1lido");const r=await fetch("/v1/media/upload/"+id,{method:"PUT",headers:{authorization:"Bearer "+t},body:f});o.textContent=JSON.stringify(await r.json());}catch(e){o.textContent="No se pudo subir el aporte";}};</script>':
    '<p>Subidas no configuradas (uploads_not_configured). Puedes registrar una URL p\u00fablica.</p>')+'</html>';
  return new Response(html,{status:configured?200:503,headers:{"content-type":"text/html;charset=utf-8",
    "cache-control":"no-store","referrer-policy":"no-referrer","x-content-type-options":"nosniff",
    "content-security-policy":"default-src 'none'; script-src 'nonce-"+nonce+"'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}});
}
export async function handleMedia(request:Request,env:MediaEnv):Promise<Response|null>{
  const path=new URL(request.url).pathname;
  if(path!=="/v1/media" && !path.startsWith("/v1/media/"))return null;
  const suffix=path.slice("/v1/media".length);
  try{
    if(suffix==="/upload" && request.method==="GET")return page(Boolean(env.CLIPOTECA_UPLOAD_TOKEN));
    if(suffix==="/upload" || suffix.startsWith("/upload/")){
      if(!env.CLIPOTECA_UPLOAD_TOKEN)return err("uploads_not_configured",503);
      if(suffix==="/upload" && request.method==="POST"){
        if(!authorized(request,env))return err("unauthorized",401);
        return await ticket(request,env);
      }
      const id=suffix.slice("/upload/".length);
      if(request.method==="PUT" && UPLOAD_ID.test(id))return await upload(request,env,id);
      return err("method_not_allowed",405);
    }
    if(!suffix && request.method==="POST"){
      if(!authorized(request,env))return err("unauthorized",401);
      return await registerUrl(request,env);
    }
    if(request.method==="GET"){
      const id=suffix.slice(1);
      if(/^u[1-9a-f][a-f0-9]{0,11}$/.test(id)){
        const response=await api(env,"/issues/"+parseInt(id.slice(1),16));
        if(response.status===404)return err("media_not_found",404);
        if(!response.ok)return err("media_registry_unavailable",503);
        const record=parseIssue(await response.json());
        return record?json(record):err("media_not_found",404);
      }
      if(UPLOAD_ID.test(id)){
        const record=await getUpload(env,id);
        return record?json(record):err("media_not_found",404);
      }
      return err("media_not_found",404);
    }
    return err("method_not_allowed",405);
  }catch(e){
    if(e instanceof TypeError || e instanceof SyntaxError)return err("invalid_media",400);
    return err("media_unavailable",503);
  }
}
