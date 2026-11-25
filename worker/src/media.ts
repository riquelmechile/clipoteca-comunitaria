/** Public contribution registry and single-use Release upload capability. */
import {inspectHeader, maxBytes, mediaUrl, PREFIX, probeLimit, scanJpeg, videoDuration, type MediaKind} from "./media-header";
import {quota} from "./quota";
import * as queue from "./upload-control";
import {SESSION_ID} from "./upload-batch-control";

export interface MediaEnv extends queue.QueueEnv {
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
  CLIPOTECA_UPLOAD_TOKEN?: string;
  CLIPOTECA_FILE_HOSTS?: string;
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
const mediaMessages: Readonly<Record<string,string>> = Object.freeze({
  invalid_media:"Datos del aporte no v\u00e1lidos.",
  invalid_title:"T\u00edtulo no v\u00e1lido.",
  invalid_author:"Autor no v\u00e1lido.",
  invalid_source:"Origen no v\u00e1lido o sin contenido.",
  invalid_license_note:"Nota de licencia no v\u00e1lida.",
  invalid_json:"Se requiere un JSON v\u00e1lido.",
  invalid_url:"Se requiere una URL HTTPS p\u00fablica y directa, sin credenciales ni par\u00e1metros.",
  invalid_length:"Longitud del archivo ausente o no v\u00e1lida.",
  invalid_range:"Respuesta de rango del origen no v\u00e1lida.",
  invalid_encoding:"Codificaci\u00f3n de respuesta del origen no admitida.",
  media_too_large:"El archivo supera el l\u00edmite de tama\u00f1o.",
  truncated_upload:"La subida entreg\u00f3 menos bytes de los esperados.",
  truncated_source:"El origen entreg\u00f3 menos bytes de los esperados.",
  invalid_media_header:"Formato del archivo no admitido o cabecera incompleta.",
  jpeg_header_too_long:"Cabecera JPEG demasiado larga.",
  image_dimensions:"Dimensiones de imagen no v\u00e1lidas o superiores a 24 MP.",
  video_duration_unknown_or_exceeds_120s:"Duraci\u00f3n desconocida o superior a 120 segundos.",
  source_unreachable:"No se pudo conectar con el origen.",
  source_read_failed:"No se pudo leer el contenido del origen.",
  media_unavailable:"El registro de aportes no est\u00e1 disponible.",
  media_registry_unavailable:"No se pudo acceder al registro de aportes.",
  media_registry_not_configured:"Registro de aportes no configurado.",
  rate_limited:"Se alcanz\u00f3 la cuota de aportes.",
});
const err = (code:string,status:number,message?:string) => json({error:{
  code,message:message ?? (Object.prototype.hasOwnProperty.call(mediaMessages,code)?mediaMessages[code]:code),
}},status);
class SourceError extends Error {
  constructor(readonly code:string,readonly status:number,readonly publicMessage?:string) {super(code);}
}
const SOURCE_USER_AGENT = "ClipotecaComunitaria/3.0 (+https://github.com/riquelmechile/clipoteca-comunitaria)";
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
async function bodyJson(request:Request,limit=8192):Promise<unknown> {
  if(!request.headers.get("content-type")?.includes("application/json") || !request.body)throw new TypeError("invalid_json");
  const reader=request.body.getReader();
  const localDecoder = new TextDecoder("utf-8",{fatal:true});
  let size=0,raw="";
  try {
    while(true){
      const part=await reader.read();
      if(part.done)break;
      size+=part.value.length;
      if(size>limit)throw new TypeError("invalid_json");
      raw+=localDecoder.decode(part.value,{stream:true});
    }
    raw+=localDecoder.decode();
    return JSON.parse(raw);
  }catch(e){
    if(e instanceof SyntaxError || e instanceof TypeError)throw new TypeError("invalid_json");
    throw e;
  }finally{await reader.cancel().catch(()=>{});}
}
function length(value:string|null):number {
  if(!value || !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))throw new TypeError("invalid_length");
  return Number(value);
}
/** Read bounded header fields. URL probes discard skipped metadata; uploads
 * must replay the consumed bytes verbatim to GitHub after validation.
 * Only uploads retain a bounded replay buffer, never an entire large file.
 */
async function readHeader(reader:ReadableStreamDefaultReader<Uint8Array>,bytes:number,kind:MediaKind,upload=false,fullValidationLater=false) {
  const limit=Math.min(bytes,probeLimit(kind));
  const replay=upload?new Uint8Array(limit):undefined;
  let consumed=0,offset=0,chunk:Uint8Array|undefined,jpeg=false;
  const transfer=async(count:number,out?:Uint8Array)=>{
    if(consumed+count>limit)throw new TypeError(jpeg && bytes>limit?"jpeg_header_too_long":"invalid_media_header");
    let copied=0;
    while(copied<count){
      if(!chunk || offset===chunk.length){
        let part:ReadableStreamReadResult<Uint8Array>;
        try{part=await reader.read();}
        catch(e){if(!upload)throw new SourceError("source_read_failed",502);throw e;}
        if(part.done)throw new TypeError(upload?"truncated_upload":"truncated_source");
        chunk=part.value;offset=0;
        if(!chunk.length)continue;
      }
      const n=Math.min(count-copied,chunk.length-offset);
      const slice=chunk.subarray(offset,offset+n);
      out?.set(slice,copied);
      replay?.set(slice,consumed);
      copied+=n;consumed+=n;offset+=n;
    }
  };
  const read=async(count:number)=>{
    const value=new Uint8Array(count);
    await transfer(count,value);
    return value;
  };
  const signature=await read(Math.min(2,limit));
  jpeg=kind==="image" && signature[0]===0xff && signature[1]===0xd8;
  let info:ReturnType<typeof checked>;
  if(jpeg){
    info={mime:await scanJpeg(read,count=>transfer(count))};
  }else{
    const head=new Uint8Array(Math.min(PREFIX,limit));
    head.set(signature);
    head.set(await read(head.length-signature.length),signature.length);
    info=fullValidationLater?{mime:inspectHeader(head,kind)}:checked(head,kind);
  }
  return {info,head:replay?.subarray(0,consumed),tail:chunk?.subarray(offset)};
}
function checked(head:Uint8Array,kind:MediaKind) {
  const mime=inspectHeader(head,kind);
  if(kind==="image")return {mime};
  const duration=videoDuration(head,mime);
  if(duration===null || duration>120)throw new TypeError("video_duration_unknown_or_exceeds_120s");
  return {mime,duration_seconds:duration};
}
async function probe(url:string,kind:MediaKind) {
  let response:Response;
  try{
    response=await fetch(url,{redirect:"manual",headers:{
      range:"bytes=0-"+(probeLimit(kind)-1),"accept-encoding":"identity","user-agent":SOURCE_USER_AGENT,
    }});
  }catch{throw new SourceError("source_unreachable",502);}
  // Never follow redirects or expose an upstream body, URL or exception.
  if(response.status!==200 && response.status!==206){
    await response.body?.cancel().catch(()=>{});
    throw new SourceError("source_status_"+response.status,400,
      "El origen respondi\u00f3 con HTTP "+response.status+".");
  }
  if(!response.body)throw new TypeError("invalid_source");
  const reader=response.body.getReader();
  try{
    const encoding=response.headers.get("content-encoding");
    if(encoding && encoding.toLowerCase()!=="identity")throw new TypeError("invalid_encoding");
    let bytes:number,expected:number;
    if(response.status===206){
      const range=/^bytes 0-(\d+)\/([1-9]\d*)$/.exec(response.headers.get("content-range")??"");
      if(!range)throw new TypeError("invalid_range");
      bytes=Number(range[2]);expected=Math.min(bytes,probeLimit(kind));
      const contentLength=response.headers.get("content-length");
      if(!Number.isSafeInteger(bytes) || Number(range[1])+1!==expected ||
        (contentLength!==null && (!/^[1-9]\d*$/.test(contentLength) || Number(contentLength)!==expected)))
        throw new TypeError("invalid_range");
    }else{
      bytes=length(response.headers.get("content-length"));expected=Math.min(bytes,probeLimit(kind));
    }
    if(bytes>maxBytes(kind))throw new TypeError("media_too_large");
    const {info}=await readHeader(reader,bytes,kind);
    return {bytes,...info};
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
  let url:string;
  try{url=mediaUrl(raw.url,meta.kind);}
  catch(e){if(e instanceof TypeError)throw new TypeError("invalid_url");throw e;}
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
const newMediaId=()=>"a"+new Date().toISOString().slice(0,7)+"-"+hex(crypto.getRandomValues(new Uint8Array(16)));
const publicReady=(env:MediaEnv)=>queue.configured(env)&&Boolean(env.CLIPOTECA_WRITE_TOKEN&&env.CLIPOTECA_WRITE_TOKEN.length>=16);
async function ticket(request:Request,env:MediaEnv,meta?:Metadata,session?:string,admitted=false) {
  meta=meta??metadata(await bodyJson(request));
  const queued=queue.configured(env);
  if(!admitted && !(queued?await queue.admit(request,env,"limit_ticket"):await quota(request,"media-ticket")))return err("rate_limited",429);
  const id=newMediaId();
  if(session){
    const reserved=await queue.batchCommand(env,session,{action:"session_add",file:{media_id:id,kind:meta.kind}});
    if(reserved.code!==200)return sessionError(reserved.code);
  }
  const exp=Date.now()+15*60*1000;
  const payload=b64(encoder.encode(JSON.stringify({...meta,id,exp})));
  const sig=b64(new Uint8Array(await crypto.subtle.sign("HMAC",await signKey(env),encoder.encode(payload))));
  const capability=payload+"."+sig;
  if(queued && (await queue.command(env,id,{action:"create",expires:exp})).code!==200)return err("media_unavailable",503);
  return json({media_id:id,status:queued?"uploading":"pending_upload",expires_at:new Date(exp).toISOString(),
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
  const token=create?env.CLIPOTECA_UPLOAD_TOKEN:env.CLIPOTECA_ISSUE_TOKEN ?? env.CLIPOTECA_UPLOAD_TOKEN;
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
    const response=await api(env,"/releases/"+r.id+"/assets?per_page=100&page="+page,env.CLIPOTECA_ISSUE_TOKEN ?? env.CLIPOTECA_UPLOAD_TOKEN);
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
async function queuedUpload(request:Request,env:MediaEnv,id:string){
  const claim=await verify(request,env,id);
  if(!claim)return err("invalid_or_expired_upload",401);
  const begin=await queue.command(env,id,{action:"begin"});
  if(begin.code!==200)return err("upload_already_used",409);
  let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
  let count=0;
  try{
    const bytes=length(request.headers.get("content-length"));
    if(bytes>maxBytes(claim.kind))throw new TypeError("media_too_large");
    if(!request.body)throw new TypeError("truncated_upload");
    if(!await queue.admit(request,env,"limit_bytes",bytes))throw new TypeError("rate_limited");
    reader=request.body.getReader();
    const {head,tail,info}=await readHeader(reader,bytes,claim.kind,true,true);
    const chunks=await queue.stage(env.UPLOADS!,id,bytes,reader,[head,tail]);
    count=chunks.length;
    const {id:_id,exp:_exp,...meta}=claim;
    const manifest:queue.Manifest={...meta,media_id:id,bytes,mime:info.mime,created_at:new Date().toISOString(),chunks};
    const cap=queue.capability();
    const ready=await queue.command(env,id,{action:"ready",manifest,capabilityHash:await queue.hash(encoder.encode(cap))});
    if(ready.code!==200)throw new Error("staging_failed");
    if(await queue.dispatch(env,id,cap)==="failed")throw new Error("dispatch_failed");
    return json({media_id:id,status:"processing"},202);
  }catch(e){
    const reason=e instanceof Error && Object.prototype.hasOwnProperty.call(queue.reasons,e.message)?e.message:"staging_failed";
    const failed=await queue.command(env,id,{action:"fail",reason});
    if(failed.code===200)await queue.discard(env,id,count);
    if(failed.code===409)return json({media_id:id,status:"processing"},202);
    return err(reason,reason==="media_too_large"?413:reason==="rate_limited"?429:e instanceof TypeError?400:503,queue.reasons[reason]);
  }finally{await reader?.cancel().catch(()=>{});}
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
    const {head,tail,info}=await readHeader(reader,bytes,claim.kind,true);
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
        if(head)await write(head);
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
    (configured?'<p>Video: 100 MiB y 120 s. Imagen: 10 MiB y 24 MP.</p><input id="file" type="file" accept="video/mp4,video/webm,video/quicktime,image/jpeg,image/png,image/webp"><button id="send">Subir aporte p\u00fablico</button><pre id="result"></pre>'+
    '<script nonce="'+nonce+'">const t=location.hash.slice(1);history.replaceState(null,"",location.pathname);document.getElementById("send").onclick=async()=>{const o=document.getElementById("result"),f=document.getElementById("file").files[0];try{if(!f||!t)throw Error("Falta archivo o enlace");const p=t.split(".")[0].replace(/-/g,"+").replace(/_/g,"/");const id=JSON.parse(atob(p)).id;if(!/^a\\d{4}-\\d{2}-[a-f0-9]{32}$/.test(id))throw Error("Enlace inv\u00e1lido");const r=await fetch("/v1/media/upload/"+id,{method:"PUT",headers:{authorization:"Bearer "+t},body:f});const v=await r.json();o.textContent=r.ok&&v.status==="processing"?"Archivo recibido. Procesando el aporte. Guarda este ID: "+id:JSON.stringify(v);}catch(e){o.textContent="No se pudo subir el aporte";}};</script>':
    '<p>Subidas no configuradas (uploads_not_configured). Puedes registrar una URL p\u00fablica.</p>')+'</html>';
  return new Response(html,{status:configured?200:503,headers:{"content-type":"text/html;charset=utf-8",
    "cache-control":"no-store","referrer-policy":"no-referrer","x-content-type-options":"nosniff",
    "content-security-policy":"default-src 'none'; script-src 'nonce-"+nonce+"'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}});
}

const sessionError=(status:number)=>err(status===410?"session_expired":status===409?"session_full":"session_unavailable",
  status===410||status===409||status===404?status:503,
  status===410?"La sesion ha vencido.":status===409?"La sesion ya tiene 12 archivos.":"Sesion no disponible.");
const mimeKind=(mime:unknown):MediaKind|undefined=>
  typeof mime==="string"&&/^(image\/(jpeg|png|webp))$/i.test(mime.split(";")[0]!)?"image":
  typeof mime==="string"&&/^(video\/(mp4|webm|quicktime))$/i.test(mime.split(";")[0]!)?"video":undefined;
const fileKind=(name:unknown):MediaKind|undefined=>typeof name==="string"?
  /\.(jpe?g|png|webp)$/i.test(name)?"image":/\.(mp4|mov|webm)$/i.test(name)?"video":undefined:undefined;
function allowedFileUrl(raw:unknown,env:MediaEnv):URL {
  if(typeof raw!=="string"||raw.length>4096)throw new TypeError("invalid_url");
  const url=new URL(raw);
  const hosts=(env.CLIPOTECA_FILE_HOSTS??"oaiusercontent.com").split(",").map(v=>v.trim().toLowerCase()).filter(Boolean);
  if(url.protocol!=="https:"||url.username||url.password||url.port||url.hash||
    !hosts.some(h=>/^[a-z0-9.-]+\.[a-z]{2,}$/.test(h)&&(url.hostname===h||url.hostname.endsWith("."+h))))throw new TypeError("invalid_url");
  return url;
}
const B64_LIMIT=40*1024;
function batchPage():Response{
  const nonce=hex(crypto.getRandomValues(new Uint8Array(16)));
  const html='<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aportar a Clipoteca</title><h1>Aporta tus videos y fotos</h1><p>'+PUBLIC_NOTICE+'</p><p>Hasta 12 archivos. Video: 100 MiB y 120 s. Imagen: 10 MiB y 24 MP.</p><label>Titulo (opcional) <input id="title" maxlength="120"></label><label>Autor (opcional) <input id="author" maxlength="120"></label><p><input id="files" type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime"></p><button id="send">Subir archivos publicos</button><button id="refresh">Consultar estado</button><pre id="out"></pre><script nonce="'+nonce+'">'+
    'const id=location.hash.slice(1);const out=document.getElementById("out");const button=document.getElementById("send");const api="/v1/uploads/"+id;const get=async(r)=>{const v=await r.json();if(!r.ok)throw Error(v.error?.message||"Error "+r.status);return v};const log=s=>out.textContent+=s+"\\n";const status=async()=>{try{const v=await get(await fetch(api));out.textContent=v.files.map(f=>f.media_id+" "+f.kind+" "+f.status+" "+(f.reason||"")).join("\\n")}catch(e){log(e.message)}};document.getElementById("refresh").onclick=status;button.onclick=async()=>{if(!/^[a-f0-9]{64}$/.test(id)){log("Enlace invalido");return}button.disabled=true;try{const files=Array.from(document.getElementById("files").files);const s=await get(await fetch(api));if(!files.length||files.length+s.files.length>12)throw Error("Selecciona hasta "+(12-s.files.length)+" archivos");for(const f of files){try{const mime=f.type;const kind=mime.startsWith("image/")||/\\.(jpe?g|png|webp)$/i.test(f.name)?"image":mime.startsWith("video/")||/\\.(mp4|mov|webm)$/i.test(f.name)?"video":null;if(!kind)throw Error("Formato no admitido");if(!f.size||f.size>(kind==="image"?10:100)*1048576)throw Error("Tamano no admitido");const t=await get(await fetch(api+"/files",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({kind,title:document.getElementById("title").value||undefined,author:document.getElementById("author").value||undefined})}));await new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open("PUT",t.put_url);x.setRequestHeader("Authorization","Bearer "+t.upload_token);x.upload.onprogress=e=>{if(e.lengthComputable)out.textContent=f.name+" "+Math.floor(100*e.loaded/e.total)+"% "+t.media_id};x.onload=()=>x.status>=200&&x.status<300?resolve():reject(Error("Subida rechazada "+x.status));x.onerror=()=>reject(Error("Error de red"));x.send(f)});log(f.name+" recibido: "+t.media_id)}catch(e){log(f.name+": "+e.message)}}}catch(e){log(e.message)}finally{button.disabled=false;await status()}};if(/^[a-f0-9]{64}$/.test(id))status();</script></html>';
  return new Response(html,{headers:{"content-type":"text/html;charset=utf-8","cache-control":"no-store","referrer-policy":"no-referrer","x-content-type-options":"nosniff","content-security-policy":"default-src 'none'; script-src 'nonce-"+nonce+"'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}});
}
async function handleBatch(request:Request,env:MediaEnv):Promise<Response>{
  const path=new URL(request.url).pathname;
  try{
    if(!publicReady(env))return err("uploads_not_configured",503,"Subidas no disponibles.");
    if(path==="/v1/uploads"){
      if(request.method==="GET")return batchPage();
      if(request.method!=="POST")return err("method_not_allowed",405);
      if(!await queue.admit(request,env,"limit_session"))return err("rate_limited",429);
      const id=queue.capability();
      const created=await queue.batchCommand(env,id,{action:"session_create"});
      if(!created.state?.expires)return sessionError(created.code);
      return json({session_id:id,expires_at:new Date(created.state.expires).toISOString(),max_files:12,
        upload_url:new URL("/v1/uploads",request.url).href+"#"+id},201);
    }
    const match=/^\/v1\/uploads\/([a-f0-9]{64})(\/files)?$/.exec(path);
    if(!match||!SESSION_ID.test(match[1]!))return sessionError(404);
    const id=match[1]!;
    const session=await queue.batchCommand(env,id,{action:"session_status"});
    if(!session.state?.files||!session.state.expires)return sessionError(session.code);
    if(!match[2]&&request.method==="GET"){
      const files=await Promise.all(session.state.files.map(async file=>{
        const response=await handleMedia(new Request(new URL("/v1/media/"+file.media_id,request.url)),env);
        if(!response?.ok)return {...file,status:response?.status===404?"rejected":"processing",
          reason:response?.status===404?"No se pudo iniciar la subida o ha vencido.":"No se pudo consultar el estado."};
        const value=await response.json<{status:string;reason?:string}>();
        return {...file,status:value.status,...(value.reason?{reason:value.reason}:{})};
      }));
      return json({session_id:id,expires_at:new Date(session.state.expires).toISOString(),files});
    }
    if(!match[2]||request.method!=="POST")return err("method_not_allowed",405);
    if(session.state.expires<=Date.now())return sessionError(410);
    if(session.state.files.length>=12)return sessionError(409);
    const raw=await bodyJson(request,60*1024);
    if(!object(raw)||Object.keys(raw).some(k=>!["kind","title","author","source","license_note","file","image_base64"].includes(k))||
       (raw.file!==undefined&&raw.image_base64!==undefined))return err("invalid_media",400);
    if(!await queue.admit(request,env,"limit_ticket"))return err("rate_limited",429);
    let remote:Response|undefined;
    let data:Uint8Array|undefined;
    let kind=raw.kind;
    try{
      if(raw.file!==undefined){
        if(!object(raw.file)||typeof raw.file.file_id!=="string"||!raw.file.file_id||raw.file.file_id.length>256)return err("invalid_media",400);
        const url=allowedFileUrl(raw.file.download_url,env);
        remote=await fetch(url.href,{redirect:"manual",signal:AbortSignal.timeout(120000),headers:{"accept-encoding":"identity"}});
        if(remote.status!==200||!remote.body)return err("source_unreachable",422,"No se pudo descargar el archivo temporal. Usa subida directa o renueva el archivo.");
        if(remote.headers.get("content-encoding")&&!/^identity$/i.test(remote.headers.get("content-encoding")!))return err("invalid_encoding",400);
        kind=kind??mimeKind(raw.file.mime_type)??mimeKind(remote.headers.get("content-type"))??fileKind(raw.file.file_name);
      }
      if(raw.image_base64!==undefined){
        if(typeof raw.image_base64!=="string"||raw.image_base64.length>Math.ceil(B64_LIMIT/3)*4||
          !/^[A-Za-z0-9+/]+={0,2}$/.test(raw.image_base64))return err("invalid_media",400,"Imagen base64 invalida o mayor de 40 KiB.");
        try{data=Uint8Array.from(atob(raw.image_base64),c=>c.charCodeAt(0));}catch{return err("invalid_media",400);}
        if(!data.length||data.length>B64_LIMIT)return err("media_too_large",413);
        kind=kind??"image";
        if(kind!=="image")return err("invalid_media",400);
      }
      const meta=metadata({kind,title:raw.title??"Aporte comunitario",author:raw.author??"Anonimo",
        source:raw.source??"Aporte de usuario",...(raw.license_note===undefined?{}:{license_note:raw.license_note})});
      let bytes:number|undefined;
      if(remote||data){
        bytes=data?.length??length(remote!.headers.get("content-length"));
        if(bytes>maxBytes(meta.kind))return err("media_too_large",413);
      }
      const response=await ticket(request,env,meta,id,true);
      if(!response.ok||bytes===undefined)return response;
      const t=await response.json<{media_id:string;put_url:string;upload_token:string}>();
      const headers=new Headers({"authorization":"Bearer "+t.upload_token,"content-length":String(bytes)});
      const ip=request.headers.get("cf-connecting-ip");if(ip)headers.set("cf-connecting-ip",ip);
      return await queuedUpload(new Request(t.put_url,{method:"PUT",headers,body:data??remote!.body}),env,t.media_id);
    }finally{if(remote?.body&&!remote.body.locked)await remote.body.cancel().catch(()=>{});}
  }catch(e){
    if(e instanceof TypeError&&Object.prototype.hasOwnProperty.call(mediaMessages,e.message))return err(e.message,400);
    return err("media_unavailable",503,"No se pudo procesar la subida.");
  }
}

export async function handleMedia(request:Request,env:MediaEnv):Promise<Response|null>{
  const path=new URL(request.url).pathname;
  if(path==="/v1/uploads"||path.startsWith("/v1/uploads/"))return handleBatch(request,env);
  if(path!=="/v1/media" && !path.startsWith("/v1/media/"))return null;
  const suffix=path.slice("/v1/media".length);
  try{
    if(suffix.startsWith("/ingest/"))return await queue.ingestRoute(request,env);
    if(suffix==="/upload" && request.method==="GET")return page(queue.configured(env)||Boolean(env.CLIPOTECA_UPLOAD_TOKEN));
    if(suffix==="/upload" || suffix.startsWith("/upload/")){
      if(!queue.configured(env) && !env.CLIPOTECA_UPLOAD_TOKEN)return err("uploads_not_configured",503);
      if(suffix==="/upload" && request.method==="POST"){
        if(!publicReady(env)&&!authorized(request,env))return err("unauthorized",401);
        return await ticket(request,env);
      }
      const id=suffix.slice("/upload/".length);
      if(request.method==="PUT" && UPLOAD_ID.test(id))return queue.configured(env)?await queuedUpload(request,env,id):await upload(request,env,id);
      return err("method_not_allowed",405);
    }
    if(!suffix && request.method==="POST"){
      if(!authorized(request,env))return err("unauthorized",401);
      return await registerUrl(request,env);
    }
    if(request.method==="GET"){
      const id=suffix.slice(1);
      if(/^u[1-9a-f][a-f0-9]{0,11}$/.test(id)){
        const response=await api(env,"/issues/"+parseInt(id.slice(1),16),env.CLIPOTECA_ISSUE_TOKEN);
        if(response.status===404)return err("media_not_found",404);
        if(!response.ok)return err("media_registry_unavailable",503);
        const record=parseIssue(await response.json());
        return record?json(record):err("media_not_found",404);
      }
      if(UPLOAD_ID.test(id)){
        const state=(await queue.command(env,id,{action:"status"})).state;
        if(!state || state.status==="registered" || state.status==="processing" ||
            state.reason==="expired" || state.reason==="publish_failed"){
          // The Release manifest is the durable registration commit point, even
          // if the workflow lost its final callback or the control state expired.
          try{
            const record=await getUpload(env,id);
            if(record)return json(record);
          }catch(e){if(!state || state.status==="registered")throw e;}
        }
        if(state)return json({media_id:id,status:state.status==="registered"?"processing":state.status,
          ...(state.reason?{reason:queue.reasons[state.reason]??queue.reasons.staging_failed}:{})});
        return err("media_not_found",404);
      }
      return err("media_not_found",404);
    }
    return err("method_not_allowed",405);
  }catch(e){
    if(e instanceof SourceError)return err(e.code,e.status,e.publicMessage);
    // Only fixed, locally defined messages are exposed. Unexpected errors are
    // infrastructure failures, not malformed user media.
    if(e instanceof TypeError && Object.prototype.hasOwnProperty.call(mediaMessages,e.message))return err(e.message,400);
    return err("media_unavailable",503);
  }
}
