/** Atomic control plane. Media bytes remain in eventually consistent KV. */
import {batchTransition,type BatchCommand,type BatchState,SESSION_ID} from "./upload-batch-control";
export const CHUNK_BYTES=24*1024*1024;
export const UPLOAD_TTL=7200;
export const ID=/^a\d{4}-\d{2}-[a-f0-9]{32}$/;
const HEX=/^[a-f0-9]{64}$/;
const AUD="clipoteca-media-ingest-v1";
const enc=new TextEncoder();
export type Chunk={bytes:number;sha256:string;keyed?:true};
export const PART_LEASE_MS=120_000;
type PendingPart={sha256:string;attempt:string;until:number};
export type Manifest={kind:"image"|"video";title:string;author:string;source:string;license_note?:string;
  description?:Record<'es'|'en',string>;tags?:Record<'es'|'en',string[]>;
  media_id:string;bytes:number;mime:string;created_at:string;chunks:Chunk[]};
export type State={status:"uploading"|"processing"|"registered"|"rejected";expires:number;started?:boolean;
  manifest?:Manifest;capabilityHash?:string;capExpires?:number;run?:string;reason?:string;
  size?:number;parts?:Record<number,Chunk>;pending?:Record<number,PendingPart|string>;mime?:string};
export interface QueueEnv {
  UPLOADS?:KVNamespace; UPLOAD_CONTROL?:DurableObjectNamespace;
  CLIPOTECA_DISPATCH_TOKEN?:string;CLIPOTECA_GITHUB_REPO?:string;
}
export const configured=(env:QueueEnv)=>Boolean(env.UPLOADS&&env.UPLOAD_CONTROL&&env.CLIPOTECA_DISPATCH_TOKEN);
const hex=(b:Uint8Array)=>Array.from(b,x=>x.toString(16).padStart(2,"0")).join("");
export const hash=async(b:BufferSource)=>hex(new Uint8Array(await crypto.subtle.digest("SHA-256",b)));
export const capability=()=>hex(crypto.getRandomValues(new Uint8Array(32)));
export const chunkKey=(id:string,i:number,sha256?:string)=>"media:"+id+":"+i+(sha256?":"+sha256:"");
const json=(body:unknown,status=200)=>Response.json(body,{status,headers:{"cache-control":"no-store","referrer-policy":"no-referrer"}});
export const reasons:Readonly<Record<string,string>>=Object.freeze({
  expired:"El enlace o plazo de procesamiento ha vencido.",
  invalid_media_header:"Formato no admitido o cabecera incompleta.",
  jpeg_header_too_long:"Cabecera JPEG demasiado larga.",
  image_dimensions:"Dimensiones no admitidas (maximo 24 MP).",
  media_too_large:"El archivo supera el limite de tamano.",
  invalid_length:"Longitud del archivo no valida.",
  truncated_upload:"La subida se interrumpio o no coincide con su longitud.",
  rate_limited:"Se alcanzo la cuota de aportes.",
  staging_failed:"No se pudo guardar el archivo temporalmente.",
  dispatch_failed:"No se pudo iniciar el procesamiento.",
  download_failed:"No se pudo recuperar el archivo temporal.",
  invalid_file:"El archivo no se pudo decodificar o excede los limites.",
  publish_failed:"No se pudo publicar el aporte.",
});
const reason=(r:unknown)=>typeof r==="string"&&Object.prototype.hasOwnProperty.call(reasons,r)?r:"staging_failed";
type Command={action:string;expires?:number;size?:number;index?:number;chunk?:Chunk;attempt?:string;mime?:string;manifest?:Manifest;capabilityHash?:string;run?:string;reason?:string;status?:string};
type Result={code:number;state?:State;write?:boolean};
export function transition(old:State|undefined,c:Command,now:number):Result {
  if(c.action==="create"){
    if(old||!c.expires||c.expires<=now)return {code:409};
    return {code:200,state:{status:"uploading",expires:c.expires,...(c.size===undefined?{}:{size:c.size})},write:true};
  }
  if(!old)return {code:404};
  if(c.action==="status"){
    if((old.status==="uploading"||old.status==="processing")&&
        (old.expires<=now||(old.status==="processing"&&(old.capExpires??0)<=now)))
      return {code:200,state:{...old,status:"rejected",reason:"expired"}};
    return {code:200,state:old};
  }
  if(old.expires<=now)return {code:410};
  if(c.action==="part_begin"||c.action==="part_done"||c.action==="part_abort"){
    const i=c.index, part=c.chunk;
    if(old.status!=="uploading"||old.started||!old.size||i===undefined||!Number.isInteger(i)||i<0||i>=Math.ceil(old.size/CHUNK_BYTES)||
      !part||part.bytes!==Math.min(CHUNK_BYTES,old.size-i*CHUNK_BYTES)||!HEX.test(part.sha256)||!c.attempt||!HEX.test(c.attempt))return {code:409};
    const parts={...old.parts},pending={...old.pending};
    if(c.action==="part_begin"){
      const previous=pending[i];
      // Legacy reservations have no timestamp: permit replacement with a fenced,
      // content-addressed write. A live reservation cannot be stolen.
      if(previous&&typeof previous!=="string"&&previous.until>now)return {code:409};
      if(parts[i]?.sha256===part.sha256)return {code:200,state:old};
      // Fence complete against an in-flight overwrite of the KV key.
      delete parts[i];pending[i]={sha256:part.sha256,attempt:c.attempt,until:now+PART_LEASE_MS};
      return {code:200,state:{...old,parts,pending},write:true};
    }
    const current=pending[i];
    if(!current||typeof current==="string"||current.sha256!==part.sha256||
      current.attempt!==c.attempt||current.until<=now)return {code:409};
    delete pending[i];
    if(c.action==="part_done"){
      if(i===0&&!c.mime)return {code:400};
      parts[i]={...part,keyed:true};
    }
    return {code:200,state:{...old,parts,pending,...(c.action==="part_done"&&i===0?{mime:c.mime!}:{})},write:true};
  }
  if(c.action==="ready_parts"){
    if(old.status!=="uploading"||old.started||!old.size||!old.mime||Object.keys(old.pending??{}).length||
      !c.manifest||c.manifest.bytes!==old.size||c.manifest.mime!==old.mime||!c.capabilityHash||!HEX.test(c.capabilityHash))return {code:409};
    const count=Math.ceil(old.size/CHUNK_BYTES);
    if(c.manifest.chunks.length!==count||!c.manifest.chunks.every((part,i)=>
      part.bytes===Math.min(CHUNK_BYTES,old.size!-i*CHUNK_BYTES)&&part.sha256===old.parts?.[i]?.sha256))return {code:409};
    return {code:200,state:{...old,status:"processing",started:true,manifest:c.manifest,capabilityHash:c.capabilityHash,
      capExpires:Math.min(old.expires,now+30*60*1000)},write:true};
  }
  if(c.action==="begin"){
    if(old.status!=="uploading"||old.started||Object.keys(old.parts??{}).length||Object.keys(old.pending??{}).length)return {code:409};
    return {code:200,state:{...old,started:true,expires:now+UPLOAD_TTL*1000},write:true};
  }
  if(c.action==="ready"){
    if(old.status!=="uploading"||!old.started||!c.manifest||!c.capabilityHash||!HEX.test(c.capabilityHash))return {code:409};
    return {code:200,state:{...old,status:"processing",manifest:c.manifest,capabilityHash:c.capabilityHash,
      capExpires:Math.min(old.expires,now+30*60*1000)},write:true};
  }
  if(c.action==="fail"){
    if(old.run||old.status==="registered"||old.status==="rejected")return {code:409};
    return {code:200,state:{...old,status:"rejected",reason:reason(c.reason)},write:true};
  }
  if(!c.run||!c.capabilityHash||c.capabilityHash!==old.capabilityHash||(old.capExpires??0)<=now)return {code:401};
  if(c.action==="claim"){
    if(old.status!=="processing"||(old.run&&old.run!==c.run))return {code:409};
    if(old.run)return {code:200,state:old};
    return {code:200,state:{...old,run:c.run},write:true};
  }
  if(old.run!==c.run)return {code:401};
  if(c.action==="read")return old.status==="processing"?{code:200,state:old}:{code:409};
  if(c.action==="finish"){
    if(c.status!=="registered"&&c.status!=="rejected")return {code:400};
    if(old.status===c.status)return {code:200,state:old};
    if(old.status!=="processing")return {code:409};
    return {code:200,state:{...old,status:c.status,...(c.status==="rejected"?{reason:reason(c.reason)}:{})},write:true};
  }
  return {code:400};
}
export class UploadControl {
  constructor(private readonly state:DurableObjectState){}
  async fetch(request:Request):Promise<Response>{
    if(request.method!=="POST")return json({},405);
    const c=await request.json<Command>();
    if(c.action.startsWith("session_")||c.action.startsWith("limit_")){
      const result=await this.state.storage.transaction(async tx=>{
        const next=batchTransition(await tx.get<BatchState>("batch"),c as BatchCommand,Date.now());
        if(next.write&&next.state)await tx.put("batch",next.state);
        return next;
      });
      if(result.write)await this.state.storage.setAlarm(Date.now()+48*3600*1000);
      return json(result.state??{},result.code);
    }
    const result=await this.state.storage.transaction(async tx=>{
      const next=transition(await tx.get<State>("state"),c,Date.now());
      if(next.write&&next.state)await tx.put("state",next.state);
      return next;
    });
    if(c.action==="create"&&result.write)await this.state.storage.setAlarm(Date.now()+24*3600*1000);
    return json(result.state??{},result.code);
  }
  async alarm():Promise<void>{await this.state.storage.deleteAll();}
}
export async function command(env:QueueEnv,id:string,c:Command):Promise<Result>{
  if(!env.UPLOAD_CONTROL||!ID.test(id))return {code:404};
  const stub=env.UPLOAD_CONTROL.get(env.UPLOAD_CONTROL.idFromName(id));
  const r=await stub.fetch("https://upload-control.internal/",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(c)});
  return r.ok?{code:r.status,state:await r.json<State>()}:{code:r.status};
}
export async function discard(env:QueueEnv,id:string,count:number,chunks?:Chunk[]){
  await Promise.allSettled(Array.from({length:count},(_,i)=>env.UPLOADS?.delete(chunkKey(id,i,chunks?.[i]?.keyed?chunks[i]!.sha256:undefined))));
}
export async function stage(kv:KVNamespace,id:string,bytes:number,
  reader:ReadableStreamDefaultReader<Uint8Array>,prefix:(Uint8Array|undefined)[]):Promise<Chunk[]>{
  const chunks:Chunk[]=[];
  const buffer=new Uint8Array(Math.min(CHUNK_BYTES,bytes));
  let used=0,total=0;
  const flush=async()=>{
    if(!used)return;
    const value=buffer.subarray(0,used);
    const sha256=await hash(value);
    await kv.put(chunkKey(id,chunks.length),value,{expirationTtl:UPLOAD_TTL});
    chunks.push({bytes:used,sha256});
    used=0;
  };
  const consume=async(part:Uint8Array)=>{
    if(total+part.length>bytes)throw new TypeError("truncated_upload");
    for(let offset=0;offset<part.length;){
      const n=Math.min(buffer.length-used,part.length-offset);
      buffer.set(part.subarray(offset,offset+n),used);
      used+=n;total+=n;offset+=n;
      if(used===buffer.length)await flush();
    }
  };
  try{
    for(const part of prefix)if(part)await consume(part);
    while(true){const part=await reader.read();if(part.done)break;await consume(part.value);}
    if(total!==bytes)throw new TypeError("truncated_upload");
    await flush();return chunks;
  }catch(e){
    await Promise.allSettled(Array.from({length:chunks.length+1},(_,i)=>kv.delete(chunkKey(id,i))));
    throw e;
  }
}
export async function dispatch(env:QueueEnv,id:string,cap:string):Promise<"queued"|"failed"|"unknown">{
  const repo=env.CLIPOTECA_GITHUB_REPO??"riquelmechile/clipoteca-comunitaria";
  if(!env.CLIPOTECA_DISPATCH_TOKEN||!/^[\w.-]+\/[\w.-]+$/.test(repo)||!ID.test(id)||!HEX.test(cap))return "failed";
  try{
    const r=await fetch("https://api.github.com/repos/"+repo+"/actions/workflows/media-ingest.yml/dispatches",{
      method:"POST",redirect:"manual",headers:{authorization:"Bearer "+env.CLIPOTECA_DISPATCH_TOKEN,
        accept:"application/vnd.github+json","content-type":"application/json","user-agent":"clipoteca-worker",
        "x-github-api-version":"2022-11-28"},
      body:JSON.stringify({ref:"main",inputs:{upload_id:id,capability:cap}})});
    return r.status===204?"queued":r.status>=500?"unknown":"failed";
  }catch{return "unknown";}
}
const decode=(s:string)=>Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/")),c=>c.charCodeAt(0));
const decodeJson=(s:string)=>JSON.parse(new TextDecoder().decode(decode(s))) as Record<string,unknown>;
/** Public workflow inputs are not credentials. A fresh GitHub OIDC proof from the main
 * workflow is required on every read and completion; the run consumes the capability. */
export async function actionsRun(request:Request,env:QueueEnv):Promise<string|null>{
  const token=request.headers.get("authorization");
  if(!token?.startsWith("Bearer ")||token.length>16384)return null;
  const repo=env.CLIPOTECA_GITHUB_REPO??"riquelmechile/clipoteca-comunitaria";
  try{
    const [head,payload,sig,...rest]=token.slice(7).split(".");
    if(!head||!payload||!sig||rest.length)return null;
    const h=decodeJson(head),c=decodeJson(payload),now=Date.now()/1000;
    if(h.alg!=="RS256"||typeof h.kid!=="string"||c.iss!=="https://token.actions.githubusercontent.com"||
      c.aud!==AUD||c.repository!==repo||c.workflow_ref!==repo+"/.github/workflows/media-ingest.yml@refs/heads/main"||
      c.ref!=="refs/heads/main"||c.event_name!=="workflow_dispatch"||typeof c.exp!=="number"||c.exp<=now||
      typeof c.nbf!=="number"||c.nbf>now+30||typeof c.run_id!=="string"||!/^\d+$/.test(c.run_id)||
      typeof c.run_attempt!=="string"||!/^\d+$/.test(c.run_attempt))return null;
    const response=await fetch("https://token.actions.githubusercontent.com/.well-known/jwks",{redirect:"manual"});
    if(!response.ok)return null;
    const keys=await response.json<{keys:(JsonWebKey&{kid?:string})[]}>();
    const jwk=keys.keys.find(k=>k.kid===h.kid&&k.kty==="RSA");
    if(!jwk)return null;
    const key=await crypto.subtle.importKey("jwk",jwk,{name:"RSASSA-PKCS1-v1_5",hash:"SHA-256"},false,["verify"]);
    const valid=await crypto.subtle.verify("RSASSA-PKCS1-v1_5",key,decode(sig),enc.encode(head+"."+payload));
    return valid?c.run_id+":"+c.run_attempt:null;
  }catch{return null;}
}
export async function ingestRoute(request:Request,env:QueueEnv):Promise<Response>{
  const m=/^\/v1\/media\/ingest\/(a\d{4}-\d{2}-[a-f0-9]{32})\/(claim|finish|chunk\/[0-4])$/.exec(new URL(request.url).pathname);
  if(!m||!configured(env))return json({},404);
  const id=m[1]!,op=m[2]!;
  if(request.method!==(op.startsWith("chunk/")?"GET":"POST"))return json({},405);
  const cap=request.headers.get("x-clipoteca-capability")??"";
  if(!HEX.test(cap))return json({},401);
  const run=await actionsRun(request,env);
  if(!run)return json({},401);
  const proof={run,capabilityHash:await hash(enc.encode(cap))};
  if(op==="claim"){
    const r=await command(env,id,{action:"claim",...proof});
    return r.state?.manifest?json(r.state.manifest):json({},r.code);
  }
  if(op==="finish"){
    const r=await command(env,id,{action:"finish",...proof,status:request.headers.get("x-clipoteca-result")??"",
      reason:request.headers.get("x-clipoteca-reason")??""});
    if(r.state)await discard(env,id,r.state.manifest?.chunks.length??0,r.state.manifest?.chunks);
    return json({},r.code);
  }
  const index=Number(op.slice(6)),r=await command(env,id,{action:"read",...proof});
  if(!r.state?.manifest?.chunks[index])return json({},r.state?404:r.code);
  const part=r.state.manifest.chunks[index]!;
  const value=await env.UPLOADS!.get(chunkKey(id,index,part.keyed?part.sha256:undefined),"stream");
  return value?new Response(value,{headers:{"content-type":"application/octet-stream","cache-control":"no-store"}}):json({},503);
}

export async function batchCommand(env:QueueEnv,id:string,c:BatchCommand):Promise<{code:number;state?:BatchState}>{
  if(!env.UPLOAD_CONTROL||!(SESSION_ID.test(id)||/^limits:\d{4}-\d{2}-\d{2}$/.test(id)))return {code:404};
  const stub=env.UPLOAD_CONTROL.get(env.UPLOAD_CONTROL.idFromName(id));
  const r=await stub.fetch("https://upload-control.internal/",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(c)});
  return r.ok?{code:r.status,state:await r.json<BatchState>()}:{code:r.status};
}
export async function admit(request:Request,env:QueueEnv,action:"limit_session"|"limit_ticket"|"limit_bytes",bytes?:number){
  const ip=await hash(enc.encode(request.headers.get("cf-connecting-ip")??"unknown"));
  const day=new Date().toISOString().slice(0,10);
  return (await batchCommand(env,"limits:"+day,{action,ip,...(bytes===undefined?{}:{bytes})})).code===200;
}
