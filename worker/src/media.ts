/** Public contribution registry and single-use Release upload capability. */
import {inspectHeader, maxBytes, mediaUrl, PREFIX, probeLimit, scanJpeg, videoDuration, type MediaKind} from "./media-header";
import {quota} from "./quota";
import {searchTokens} from "./catalog";
import * as queue from "./upload-control";
import {SESSION_ID} from "./upload-batch-control";

export interface MediaEnv extends queue.QueueEnv {
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
  CLIPOTECA_ISSUE_TOKEN?: string;
  CLIPOTECA_UPLOAD_TOKEN?: string;
  CLIPOTECA_FILE_HOSTS?: string;
  WAIT_FETCH?:typeof fetch;
  WAIT_FAST_MEDIA?:boolean;
}
export const PUBLIC_NOTICE = "Todo lo que subas (archivos, guion y video final) queda p\u00fablico.";
type Descriptive = {description:{es:string;en?:string};tags?:Partial<Record<'es'|'en',string[]>>};
type Metadata = Partial<Descriptive> & {kind: MediaKind; title: string; author: string; source: string; license_note?: string};
type RecordMedia = Metadata & {
  media_id: string; bytes: number; mime: string; origin: "url" | "upload";
  url: string; created_at: string; status: "registered"; duration_seconds?: number;
  width?:number;height?:number;low_resolution?:boolean;
};
type Ticket = Metadata & {id:string; exp:number; size?:number};
const SINGLE_PUT_MAX=95*1024*1024;
type Asset = {name:string; size:number; state:string; browser_download_url:string};
type Release = {id:number; assets:Asset[]};
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const json = (value:unknown,status=200) => Response.json(value,{status,headers:{"cache-control":"no-store","referrer-policy":"no-referrer"}});
const mediaMessages: Readonly<Record<string,string>> = Object.freeze({
  invalid_media:"Datos del aporte no v\u00e1lidos.",
  invalid_description:"Describe lo que se ve en espanol (maximo 400 caracteres).",
  invalid_tags:"Tags opcionales (1 a 8 por idioma, maximo 40 caracteres).",
  invalid_title:"T\u00edtulo no v\u00e1lido.",
  invalid_author:"Autor no v\u00e1lido.",
  invalid_source:"Origen no v\u00e1lido o sin contenido.",
  invalid_license_note:"Nota de licencia no v\u00e1lida.",
  invalid_json:"Se requiere un JSON v\u00e1lido.",
  invalid_url:"Se requiere una URL HTTPS p\u00fablica y directa, sin credenciales ni par\u00e1metros.",
  use_parts:"Archivos de mas de 95 MiB: usa la subida por partes (PUT /parts/n y POST /complete).",
  parts_missing:"Faltan partes o hay una parte en curso. Reintenta antes de completar.",
  invalid_part_size:"El tamano de la parte no coincide con el ticket.",
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
  (env.WAIT_FETCH??fetch)("https://api.github.com/repos/"+repo(env)+path,{
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
  if(!object(x) || Object.keys(x).some(k=>!["kind","title","author","source","license_note","description","tags",...extra].includes(k)) ||
    (x.kind!=="video" && x.kind!=="image"))throw new TypeError("invalid_media");
  const field=(name:string,max:number)=>{
    const value=x[name];
    if(typeof value!=="string" || !value.trim() || value.length>max || /[\x00-\x1f\x7f]/.test(value))
      throw new TypeError("invalid_"+name);
    return value.trim();
  };
  const clean=(v:unknown,max:number)=>typeof v==="string"&&v.trim().length>0&&v.length<=max&&!/[\x00-\x1f\x7f]/.test(v);
  let descriptive:Partial<Descriptive>={};
  if(x.description!==undefined||x.tags!==undefined){
    const d=x.description, tags=x.tags;
    if(!object(d)||Object.keys(d).some(k=>k!=="es"&&k!=="en")||
      !clean(d.es,400)||(d.en!==undefined&&!clean(d.en,400)))throw new TypeError("invalid_description");
    descriptive.description={es:(d.es as string).trim(),
      ...(d.en===undefined?{}:{en:(d.en as string).trim()})};
    if(tags!==undefined){
      if(!object(tags)||Object.keys(tags).some(k=>k!=="es"&&k!=="en")||
        !Object.values(tags).every(v=>Array.isArray(v)&&v.length>=1&&v.length<=8&&v.every(s=>clean(s,40))))
        throw new TypeError("invalid_tags");
      descriptive.tags={
        ...(tags.es===undefined?{}:{es:(tags.es as string[]).map(s=>s.trim())}),
        ...(tags.en===undefined?{}:{en:(tags.en as string[]).map(s=>s.trim())}),
      };
    }
  }
  return {...descriptive,kind:x.kind,title:field("title",120),author:field("author",120),source:field("source",500),
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
async function readHeader(reader:ReadableStreamDefaultReader<Uint8Array>,bytes:number,kind:MediaKind,upload=false,fullValidationLater=false,unknownLength=false) {
  const limit=Math.min(bytes,probeLimit(kind));
  const replay=upload?new Uint8Array(limit):undefined;
  let consumed=0,offset=0,chunk:Uint8Array|undefined,jpeg=false;
  const transfer=async(count:number,out?:Uint8Array,allowEof=false)=>{
    if(consumed+count>limit)throw new TypeError(jpeg && bytes>limit?"jpeg_header_too_long":"invalid_media_header");
    let copied=0;
    while(copied<count){
      if(!chunk || offset===chunk.length){
        let part:ReadableStreamReadResult<Uint8Array>;
        try{part=await reader.read();}
        catch(e){if(!upload)throw new SourceError("source_read_failed",502);throw e;}
        if(part.done){if(allowEof)return copied;throw new TypeError(upload?"truncated_upload":"truncated_source");}
        chunk=part.value;offset=0;
        if(!chunk.length)continue;
      }
      const n=Math.min(count-copied,chunk.length-offset);
      const slice=chunk.subarray(offset,offset+n);
      out?.set(slice,copied);
      replay?.set(slice,consumed);
      copied+=n;consumed+=n;offset+=n;
    }
    return copied;
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
    info={mime:await scanJpeg(read,async count=>{await transfer(count);})};
  }else{
    const head=new Uint8Array(Math.min(PREFIX,limit));
    head.set(signature);
    const count=await transfer(head.length-signature.length,head.subarray(signature.length),unknownLength);
    const actual=head.subarray(0,signature.length+count);
    info=fullValidationLater?{mime:inspectHeader(actual,kind)}:checked(actual,kind);
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
async function ticket(request:Request,env:MediaEnv,meta?:Metadata,session?:string,admitted=false,size?:number,unknownLength=false) {
  const queued=queue.configured(env);
  if(!meta){
    const raw=await bodyJson(request);
    meta=metadata(raw,["size"]);
    if(object(raw))size=raw.size as number|undefined;
  }
  if(queued&&!meta.description?.es)return err("invalid_description",400);
  if(((queued&&!unknownLength)||size!==undefined)&&(!Number.isSafeInteger(size)||size!<=0))return err("invalid_length",400);
  if(size!==undefined&&size>maxBytes(meta.kind))return err("media_too_large",413);
  if(!queued&&size!==undefined&&size>SINGLE_PUT_MAX)return err("use_parts",413);
  if(!admitted && !(queued?await queue.admit(request,env,"limit_ticket"):await quota(request,"media-ticket")))return err("rate_limited",429);
  // Unknown lengths reserve the maximum before reading; no unmetered staging.
  if(queued&&!await queue.admit(request,env,"limit_bytes",unknownLength?maxBytes(meta.kind):size))return err("rate_limited",429);
  const id=newMediaId();
  if(session){
    const reserved=await queue.batchCommand(env,session,{action:"session_add",file:{media_id:id,kind:meta.kind}});
    if(reserved.code!==200)return sessionError(reserved.code);
  }
  const exp=Date.now()+(queued?queue.UPLOAD_TTL:15*60)*1000;
  const payload=b64(encoder.encode(JSON.stringify({...meta,id,exp,...(size===undefined?{}:{size})})));
  const sig=b64(new Uint8Array(await crypto.subtle.sign("HMAC",await signKey(env),encoder.encode(payload))));
  const capability=payload+"."+sig;
  if(queued && (await queue.command(env,id,{action:"create",expires:exp,kind:meta.kind,...(size===undefined?{}:{size})})).code!==200)return err("media_unavailable",503);
  return json({media_id:id,status:queued?"uploading":"pending_upload",expires_at:new Date(exp).toISOString(),
    upload_url:new URL("/v1/media/upload",request.url).href+"#"+capability,
    put_url:new URL("/v1/media/upload/"+id,request.url).href,upload_token:capability,
    ...(queued&&size!==undefined?{part_size:queue.CHUNK_BYTES,parts:Math.ceil(size/queue.CHUNK_BYTES)}:{})},201);
}
async function verify(request:Request,env:MediaEnv,id:string):Promise<Ticket|null>{
  const token=request.headers.get("authorization")?.slice(7)??"";
  if(!request.headers.get("authorization")?.startsWith("Bearer ") || token.length>16384 ||
    !env.CLIPOTECA_WRITE_TOKEN || env.CLIPOTECA_WRITE_TOKEN.length<16)return null;
  const [payload,sig,...rest]=token.split(".");
  if(!payload||!sig||rest.length)return null;
  try{
    if(!await crypto.subtle.verify("HMAC",await signKey(env),unb64(sig),encoder.encode(payload)))return null;
    const value=JSON.parse(decoder.decode(unb64(payload))) as Ticket;
    if(value.id!==id || !Number.isSafeInteger(value.exp) || value.exp<=Date.now())return null;
    metadata(value,["id","exp","size"]);if(value.size!==undefined&&(!Number.isSafeInteger(value.size)||value.size<=0||value.size>maxBytes(value.kind)))return null;return value;
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
  if(env.WAIT_FETCH){
    // Exact immutable manifest: no release-asset pagination per status poll.
    const response=await env.WAIT_FETCH("https://github.com/"+repo(env)+"/releases/download/"+tag(id)+"/"+id+".json",{redirect:"follow"});
    if(response.status===404){await response.body?.cancel();return null;}
    if(!response.ok)throw new Error("media_registry_unavailable");
    const value=await response.json() as RecordMedia;
    if(value.media_id!==id||value.origin!=="upload"||value.status!=="registered")throw new Error("invalid_manifest");
    return typeof value.width==="number"&&typeof value.height==="number"
      ? {...value,low_resolution:Math.min(value.width,value.height)<720}:value;
  }
  const r=await release(env,id);
  if(!r)return null;
  const found=await asset(env,r,id+".json");
  if(!found || found.state!=="uploaded")return null;
  // Construct the trusted public Release URL ourselves; no token on redirects.
  const response=await fetch("https://github.com/"+repo(env)+"/releases/download/"+tag(id)+"/"+id+".json");
  if(!response.ok)throw new Error("media_registry_unavailable");
  const value=await response.json() as RecordMedia;
  if(value.media_id!==id || value.origin!=="upload")throw new Error("invalid_manifest");
  return typeof value.width==="number"&&typeof value.height==="number"
    ? {...value,low_resolution:Math.min(value.width,value.height)<720}:value;
}
async function queuedUpload(request:Request,env:MediaEnv,id:string,body=request.body,internal=false){
  const claim=await verify(request,env,id);
  if(!claim)return err("invalid_or_expired_upload",401);
  const rawLength=request.headers.get("content-length");
  const unknownLength=internal&&rawLength===null&&claim.size===undefined;
  const declared=Number(rawLength);
  if(!internal&&declared>SINGLE_PUT_MAX)return err("use_parts",413);
  if(claim.size!==undefined&&declared!==claim.size)return err("invalid_length",400);
  const begin=await queue.command(env,id,{action:"begin"});
  if(begin.code!==200)return err("upload_already_used",409);
  let reader:ReadableStreamDefaultReader<Uint8Array>|undefined;
  let count=0;
  try{
    const limit=unknownLength?maxBytes(claim.kind):length(rawLength);
    if(limit>maxBytes(claim.kind))throw new TypeError("media_too_large");
    if(!body)throw new TypeError("truncated_upload");
    if(claim.size===undefined&&!unknownLength&&!await queue.admit(request,env,"limit_bytes",limit))throw new TypeError("rate_limited");
    reader=body.getReader();
    const {head,tail,info}=await readHeader(reader,limit,claim.kind,true,true,unknownLength);
    const chunks=await queue.stage(env.UPLOADS!,id,limit,reader,[head,tail],unknownLength);
    count=chunks.length;
    const bytes=chunks.reduce((sum,chunk)=>sum+chunk.bytes,0);
    const {id:_id,exp:_exp,size:_size,...meta}=claim;
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
async function uploadPart(request:Request,env:MediaEnv,id:string,index:number){
  const claim=await verify(request,env,id);
  if(!claim||claim.size===undefined)return err("invalid_or_expired_upload",401);
  const count=Math.ceil(claim.size/queue.CHUNK_BYTES);
  if(index<0||index>=count)return err("invalid_part_size",400);
  const bytes=Math.min(queue.CHUNK_BYTES,claim.size-index*queue.CHUNK_BYTES);
  if(request.headers.get("content-length")!==String(bytes)||!request.body)return err("invalid_part_size",400);
  const reader=request.body.getReader();
  let chunk:queue.Chunk|undefined,locked=false;
  const attempt=queue.capability();
  try{
    const buffer=new Uint8Array(bytes);
    let total=0;
    while(true){
      const next=await reader.read();
      if(next.done)break;
      if(total+next.value.length>bytes)return err("invalid_part_size",400);
      buffer.set(next.value,total);total+=next.value.length;
    }
    if(total!==bytes)return err("invalid_part_size",400);
    let mime:string|undefined;
    if(index===0){
      const header=new Response(buffer).body!.getReader();
      try{mime=(await readHeader(header,bytes,claim.kind,false,true)).info.mime;}
      finally{await header.cancel().catch(()=>{});}
    }
    chunk={bytes,sha256:await queue.hash(buffer)};
    const begin=await queue.command(env,id,{action:"part_begin",index,chunk,attempt});
    if(begin.code!==200)return err("upload_already_used",begin.code===410?410:409);
    if(begin.state?.parts?.[index]?.sha256===chunk.sha256)return json({part:index,...chunk});
    locked=true;
    await env.UPLOADS!.put(queue.chunkKey(id,index,chunk.sha256),buffer,{expirationTtl:queue.UPLOAD_TTL});
    const done=await queue.command(env,id,{action:"part_done",index,chunk,attempt,...(mime===undefined?{}:{mime})});
    if(done.code!==200)return err("media_unavailable",503);
    locked=false;
    return json({part:index,...chunk});
  }catch(e){
    if(e instanceof TypeError&&Object.prototype.hasOwnProperty.call(mediaMessages,e.message))return err(e.message,400);
    return err("media_unavailable",503);
  }finally{
    if(locked&&chunk)await queue.command(env,id,{action:"part_abort",index,chunk,attempt}).catch(()=>{});
    await reader.cancel().catch(()=>{});
  }
}
async function completeParts(request:Request,env:MediaEnv,id:string){
  const claim=await verify(request,env,id);
  if(!claim||claim.size===undefined)return err("invalid_or_expired_upload",401);
  const state=(await queue.command(env,id,{action:"status"})).state;
  if(!state||state.status!=="uploading")return err("upload_already_used",409);
  const chunks:queue.Chunk[]=[];
  for(let i=0;i<Math.ceil(claim.size/queue.CHUNK_BYTES);i++){
    const part=state.parts?.[i];
    if(!part||part.bytes!==Math.min(queue.CHUNK_BYTES,claim.size-i*queue.CHUNK_BYTES))return err("parts_missing",409);
    chunks.push(part);
  }
  if(!state.mime)return err("parts_missing",409);
  const {id:_id,exp:_exp,size,...meta}=claim;
  const manifest:queue.Manifest={...meta,media_id:id,bytes:size!,mime:state.mime,created_at:new Date().toISOString(),chunks};
  const cap=queue.capability();
  const ready=await queue.command(env,id,{action:"ready_parts",manifest,capabilityHash:await queue.hash(encoder.encode(cap))});
  if(ready.code!==200)return err("parts_missing",409);
  if(await queue.dispatch(env,id,cap)==="failed"){
    const failed=await queue.command(env,id,{action:"fail",reason:"dispatch_failed"});
    if(failed.code===200)await queue.discard(env,id,chunks.length,chunks);
    return err("dispatch_failed",503,queue.reasons.dispatch_failed);
  }
  return json({media_id:id,status:"processing"},202);
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
    if(bytes>SINGLE_PUT_MAX)return err("use_parts",413);
    if(claim.size!==undefined&&bytes!==claim.size)return err("invalid_length",400);
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
    const {id:_id,exp:_exp,size:_size,...meta}=claim;
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
    '<script nonce="'+nonce+'">const t=location.hash.slice(1);history.replaceState(null,"",location.pathname);document.getElementById("send").onclick=async()=>{const o=document.getElementById("result"),f=document.getElementById("file").files[0];try{if(!f||!t)throw Error("Falta archivo o enlace");const p=t.split(".")[0].replace(/-/g,"+").replace(/_/g,"/");const ticket=JSON.parse(atob(p));const id=ticket.id;if(!/^a\\d{4}-\\d{2}-[a-f0-9]{32}$/.test(id))throw Error("Enlace inv\u00e1lido");const url="/v1/media/upload/"+id;const headers={authorization:"Bearer "+t};let r;if(ticket.size!==undefined&&ticket.size!==f.size)throw Error("El tamano no coincide con el ticket");if(ticket.size!==undefined&&f.size>95*1048576){for(let n=0;n<Math.ceil(f.size/(24*1048576));n++){const part=f.slice(n*24*1048576,(n+1)*24*1048576);for(let attempt=0;;attempt++){try{const response=await fetch(url+"/parts/"+n,{method:"PUT",headers,body:part});if(!response.ok)throw Error("Subida rechazada "+response.status);break}catch(e){if(attempt===2)throw e;await new Promise(r=>setTimeout(r,1500*(attempt+1)))}}o.textContent=Math.floor(100*Math.min(f.size,(n+1)*24*1048576)/f.size)+"%"}r=await fetch(url+"/complete",{method:"POST",headers})}else{r=await fetch(url,{method:"PUT",headers,body:f})}const v=await r.json();o.textContent=r.ok&&v.status==="processing"?"Archivo recibido. Procesando el aporte. Guarda este ID: "+id:JSON.stringify(v);}catch(e){o.textContent="No se pudo subir el aporte";}};</script>':
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
// Only report a parsed hostname; never include URLs, paths, query strings or errors from fetch.
function allowedFileUrl(raw:unknown,env:MediaEnv,base?:URL):URL {
  const code=base?"redirect":"invalid_url";
  if(typeof raw!=="string"||!raw||raw.length>4096)throw new SourceError(code,400,"URL ausente o invalida.");
  let url:URL;
  try{url=new URL(raw,base);}catch{throw new SourceError(code,400,"URL invalida.");}
  const host=url.hostname.toLowerCase();
  // CLIPOTECA_FILE_HOSTS, if set, is a strict allowlist. By default any public HTTPS host
  // is accepted: hosts rotate their signed-file domains, and this is no broader than
  // registering a public URL with POST /v1/media. Bytes are still fully validated.
  const strict=env.CLIPOTECA_FILE_HOSTS?.split(",").map(v=>v.trim().toLowerCase()).filter(Boolean);
  const allowed=strict?.length
    ?strict.some(h=>/^[a-z0-9.-]+\.[a-z]{2,}$/.test(h)&&(host===h||host.endsWith("."+h)))
    :/^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/.test(host)&&host.length<=253&&
      !/(^|\.)(localhost|local|internal|invalid|test|example|onion|arpa)$/.test(host);
  console.log(JSON.stringify({event:"file_source_host",host,allowed:Boolean(allowed),redirect:Boolean(base)}));
  if(url.protocol!=="https:"||url.username||url.password||(url.port&&url.port!=="443")||url.hash||!allowed)
    throw new SourceError(code,400,"Host o URL no permitido: "+(host||"(sin host)")+".");
  return url;
}
async function downloadFile(raw:unknown,env:MediaEnv):Promise<Response>{
  let url=allowedFileUrl(raw,env);
  const signal=AbortSignal.timeout(120000);
  for(let hops=0;;hops++){
    let response:Response;
    try{response=await fetch(url.href,{redirect:"manual",signal,headers:{"accept-encoding":"identity"}});}
    catch{throw new SourceError("source_unreachable",502,"No se pudo conectar con el host "+url.hostname+".");}
    if([301,302,303,307,308].includes(response.status)){
      const location=response.headers.get("location");
      await response.body?.cancel().catch(()=>{});
      const next=allowedFileUrl(location,env,url);
      if(hops>=3)throw new SourceError("redirect",400,"Maximo 3 redirecciones; destino: "+next.hostname+".");
      url=next;continue;
    }
    if(response.status!==200||!response.body){
      await response.body?.cancel().catch(()=>{});
      throw new SourceError("source_status_"+response.status,422,"Origen "+url.hostname+": HTTP "+response.status+(response.status===200?" sin cuerpo.":"."));
    }
    return response;
  }
}
const B64_LIMIT=40*1024;
function batchPage():Response{
  const nonce=hex(crypto.getRandomValues(new Uint8Array(16)));
  const html='<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aportar a Clipoteca</title><h1>Aporta tus videos y fotos</h1><p>'+PUBLIC_NOTICE+'</p><p>Hasta 12 archivos. Video: 100 MiB y 120 s. Imagen: 10 MiB y 24 MP.</p><label>Titulo (opcional) <input id="title" maxlength="120"></label><label>Autor (opcional) <input id="author" maxlength="120"></label><p><input id="files" type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,video/quicktime"></p><div id="metadata"></div><button id="send">Subir archivos publicos</button><button id="refresh">Consultar estado</button><pre id="out"></pre><pre id="state"></pre><script nonce="'+nonce+'">'+
    'const id=location.hash.slice(1);const out=document.getElementById("out");const button=document.getElementById("send");const api="/v1/uploads/"+id;const input=document.getElementById("files");const fields=document.getElementById("metadata");const formRows=[];input.onchange=()=>{fields.replaceChildren();formRows.length=0;for(const f of input.files){const row=document.createElement("fieldset");const name=document.createElement("legend");name.textContent=f.name;row.append(name);const label=document.createElement("label");label.textContent="\\u00bfQu\\u00e9 se ve? (obligatorio) ";const desc=document.createElement("textarea");desc.required=true;desc.maxLength=400;desc.rows=3;label.append(desc);row.append(label);const tagLabel=document.createElement("label");tagLabel.textContent=" Tags (opcional, separados por coma) ";const tags=document.createElement("input");tags.type="text";tags.maxLength=328;tagLabel.append(tags);row.append(tagLabel);fields.append(row);formRows.push({file:f,desc,tags})}};const get=async(r)=>{const v=await r.json();if(!r.ok)throw Error(v.error?.message||"Error "+r.status);return v};const log=s=>out.textContent+=s+"\\n";const state=document.getElementById("state");const status=async()=>{try{const v=await get(await fetch(api));state.textContent=v.files.length?"Estado del lote:\\n"+v.files.map(f=>f.media_id+" "+f.kind+" "+f.status+" "+(f.reason||"")).join("\\n"):""}catch(e){log(e.message)}};document.getElementById("refresh").onclick=status;button.onclick=async()=>{if(!/^[a-f0-9]{64}$/.test(id)){log("Enlace invalido");return}button.disabled=true;out.textContent="Subiendo...\\n";try{const files=Array.from(document.getElementById("files").files);if(formRows.length!==files.length||formRows.some(r=>!r.desc.value.trim()))throw Error("Describe lo que se ve en cada archivo.");const total=files.reduce((n,f)=>n+f.size,0);let uploaded=0;const s=await get(await fetch(api));if(!files.length||files.length+s.files.length>12)throw Error("Selecciona hasta "+(12-s.files.length)+" archivos");for(const entry of formRows){const f=entry.file;try{const mime=f.type;const kind=mime.startsWith("image/")||/\\.(jpe?g|png|webp)$/i.test(f.name)?"image":mime.startsWith("video/")||/\\.(mp4|mov|webm)$/i.test(f.name)?"video":null;if(!kind)throw Error("Formato no admitido");if(!f.size||f.size>(kind==="image"?10:100)*1048576)throw Error("pesa "+Math.round(f.size/1048576)+" MB y el maximo es "+(kind==="image"?10:100)+" MB");const t=await get(await fetch(api+"/files",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({kind,size:f.size,title:document.getElementById("title").value||undefined,author:document.getElementById("author").value||undefined,description:{es:entry.desc.value.trim()},...(entry.tags.value.trim()?{tags:{es:entry.tags.value.split(",").map(s=>s.trim()).filter(Boolean)}}:{})})}));for(let n=0;n<t.parts;n++){const offset=n*t.part_size;const part=f.slice(offset,offset+t.part_size);for(let attempt=0;;attempt++){try{await new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open("PUT",t.put_url+"/parts/"+n);x.setRequestHeader("Authorization","Bearer "+t.upload_token);x.upload.onprogress=e=>{if(e.lengthComputable)out.textContent=f.name+" "+Math.floor(100*(offset+e.loaded)/f.size)+"% (lote "+Math.floor(100*(uploaded+offset+e.loaded)/total)+"%) "+t.media_id};x.onload=()=>x.status>=200&&x.status<300?resolve():reject(Error("Subida rechazada "+x.status));x.onerror=()=>reject(Error("Error de red"));x.ontimeout=()=>reject(Error("Tiempo de espera agotado"));x.timeout=180000;x.send(part)});break}catch(e){if(attempt===2)throw e;await new Promise(r=>setTimeout(r,1500*(attempt+1)))}}}await get(await fetch(t.put_url+"/complete",{method:"POST",headers:{authorization:"Bearer "+t.upload_token}}));uploaded+=f.size;log(f.name+" recibido: "+t.media_id)}catch(e){log(f.name+": "+e.message)}}}catch(e){log(e.message)}finally{button.disabled=false;await status()}};if(/^[a-f0-9]{64}$/.test(id))status();</script></html>';
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
      const files=[];
      for(const file of session.state.files){
        const response=await handleMedia(new Request(new URL("/v1/media/"+file.media_id,request.url)),env);
        if(!response?.ok){files.push({...file,status:response?.status===404?"rejected":"processing",
          reason:response?.status===404?"No se pudo iniciar la subida o ha vencido.":"No se pudo consultar el estado."});continue;}
        const value=await response.json<{status:string;reason?:string;low_resolution?:boolean;width?:number;height?:number}>();
        files.push({...file,status:value.status,...(value.reason?{reason:value.reason}:{}),
          ...(typeof value.low_resolution==="boolean"?{low_resolution:value.low_resolution}:{}),
          ...(typeof value.width==="number"?{width:value.width}:{}),...(typeof value.height==="number"?{height:value.height}:{})});
      }
      return json({session_id:id,expires_at:new Date(session.state.expires).toISOString(),files});
    }
    if(!match[2]||request.method!=="POST")return err("method_not_allowed",405);
    if(session.state.expires<=Date.now())return sessionError(410);
    if(session.state.files.length>=12)return sessionError(409);
    const raw=await bodyJson(request,60*1024);
    if(!object(raw)||Object.keys(raw).some(k=>!["kind","title","author","source","license_note","description","tags","file","image_base64","size"].includes(k))||
       (raw.file!==undefined&&raw.image_base64!==undefined))return err("invalid_media",400);
    if(!await queue.admit(request,env,"limit_ticket"))return err("rate_limited",429);
    let remote:Response|undefined;
    let data:Uint8Array<ArrayBuffer>|undefined;
    let kind=raw.kind;
    try{
      if(raw.file!==undefined){
        if(typeof raw.file==="string"||(object(raw.file)&&typeof raw.file.file_id==="string"&&!raw.file.download_url))
          return err("missing_download_url",400,"El host entrego file_id sin download_url. Solicita al host una referencia temporal completa; no inventes URL.");
        if(!object(raw.file)||typeof raw.file.file_id!=="string"||!raw.file.file_id||raw.file.file_id.length>256)return err("invalid_media",400,"Se requiere un objeto file con file_id y download_url.");
        remote=await downloadFile(raw.file.download_url,env);
        if(remote.headers.get("content-encoding")&&!/^identity$/i.test(remote.headers.get("content-encoding")!))return err("invalid_encoding",400,"El origen devolvio content-encoding distinto de identity.");
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
        source:raw.source??"Aporte de usuario",description:raw.description,tags:raw.tags,...(raw.license_note===undefined?{}:{license_note:raw.license_note})});
      let bytes:number|undefined;
      const header=remote?.headers.get("content-length");
      const unknownLength=Boolean(remote&&header===null);
      if(remote||data){
        if(data)bytes=data.length;
        else if(!unknownLength){
          try{bytes=length(header??null);}catch{return err("invalid_length",400,"Content-Length del origen invalido.");}
        }
        if(bytes!==undefined&&bytes>maxBytes(meta.kind))return err("media_too_large",413);
      }
      if(raw.size!==undefined&&(unknownLength||(bytes!==undefined&&raw.size!==bytes)))return err("invalid_length",400,"No se puede verificar size contra Content-Length del origen.");
      const response=await ticket(request,env,meta,id,true,bytes??(raw.size as number|undefined),unknownLength);
      if(!response.ok||(!remote&&!data))return response;
      const t=await response.json<{media_id:string;put_url:string;upload_token:string}>();
      const headers=new Headers({"authorization":"Bearer "+t.upload_token});
      if(bytes!==undefined)headers.set("content-length",String(bytes));
      const ip=request.headers.get("cf-connecting-ip");if(ip)headers.set("cf-connecting-ip",ip);
      // Do not wrap an upstream stream in a synthetic Request (Node requires duplex).
      const stream=data?new Response(data).body:remote!.body;
      return await queuedUpload(new Request(t.put_url,{method:"PUT",headers}),env,t.media_id,stream,true);
    }finally{if(remote?.body&&!remote.body.locked)await remote.body.cancel().catch(()=>{});}
  }catch(e){
    if(e instanceof SourceError)return err(e.code,e.status,e.publicMessage);
    if(e instanceof TypeError&&Object.prototype.hasOwnProperty.call(mediaMessages,e.message))return err(e.message,400);
    return err("media_unavailable",503,"No se pudo procesar la subida.");
  }
}

async function searchContributions(request:Request,env:MediaEnv):Promise<Response>{
  const query=new URL(request.url).searchParams.get("q")??"";
  const terms=searchTokens(query);
  if(query.length>200||!terms.length)return err("invalid_media",400,"Describe el aporte buscado.");
  const source=await fetch("https://raw.githubusercontent.com/"+repo(env)+"/main/data/aportes.json",
    {redirect:"manual",headers:{"cache-control":"no-cache","user-agent":"clipoteca-worker"}});
  if(!source.ok)return err("media_registry_unavailable",503);
  const rows=await source.json<unknown>();
  if(!Array.isArray(rows))return err("media_registry_unavailable",503);
  const items=[];
  for(const row of rows){
    if(!object(row)||row.status!=="registered"||typeof row.media_id!=="string"||
      !UPLOAD_ID.test(row.media_id)||!object(row.description)||typeof row.description.es!=="string")continue;
    const values=[row.title,row.source,...Object.values(row.description),...Object.values(object(row.tags)?row.tags:{}).flat()];
    const vocabulary=new Set(searchTokens(values.filter(v=>typeof v==="string").join(" ")));
    if(terms.every(term=>vocabulary.has(term)))items.push(row);
  }
  return json({items:items.slice(0,50)});
}

export async function handleMedia(request:Request,env:MediaEnv):Promise<Response|null>{
  const path=new URL(request.url).pathname;
  if(path==="/v1/uploads"||path.startsWith("/v1/uploads/"))return handleBatch(request,env);
  if(path!=="/v1/media" && !path.startsWith("/v1/media/"))return null;
  const suffix=path.slice("/v1/media".length);
  try{
    if(suffix==="/search"&&request.method==="GET")return await searchContributions(request,env);
    if(suffix.startsWith("/ingest/"))return await queue.ingestRoute(request,env);
    if(suffix==="/upload" && request.method==="GET")return page(queue.configured(env)||Boolean(env.CLIPOTECA_UPLOAD_TOKEN));
    if(suffix==="/upload" || suffix.startsWith("/upload/")){
      if(!queue.configured(env) && !env.CLIPOTECA_UPLOAD_TOKEN)return err("uploads_not_configured",503);
      if(suffix==="/upload" && request.method==="POST"){
        if(!publicReady(env)&&!authorized(request,env))return err("unauthorized",401);
        return await ticket(request,env);
      }
      const multipart=/^\/upload\/(a\d{4}-\d{2}-[a-f0-9]{32})\/(parts\/([0-4])|complete)$/.exec(suffix);
      if(multipart&&queue.configured(env)){
        if(multipart[2]==="complete"&&request.method==="POST")return await completeParts(request,env,multipart[1]!);
        if(multipart[3]!==undefined&&request.method==="PUT")return await uploadPart(request,env,multipart[1]!,Number(multipart[3]));
        return err("method_not_allowed",405);
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
        if(!state || state.status==="registered" || (state.status==="processing"&&!env.WAIT_FAST_MEDIA) ||
            state.reason==="expired" || state.reason==="publish_failed"){
          // The Release manifest is the durable registration commit point, even
          // if the workflow lost its final callback or the control state expired.
          try{
            const record=await getUpload(env,id);
            if(record)return json(record);
          }catch(e){if(!state || state.status==="registered")throw e;}
        }
        if(state)return json({media_id:id,status:state.status==="registered"?"processing":state.status,
          ...(state.manifest?.kind||state.kind?{kind:state.manifest?.kind??state.kind}:{}),
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
