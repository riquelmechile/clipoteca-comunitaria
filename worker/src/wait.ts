/** Read-only long polling. No waitUntil: cancellation owns the lifetime. */
import type {RestEnv} from "./rest";
import {handleVideos} from "./videos";
import {handleMedia} from "./media";

const VIDEO=/^[a-f0-9]{32}$/;
const SESSION=/^[a-f0-9]{64}$/;
const MEDIA=/^(?:u[1-9a-f][a-f0-9]{0,11}|a\d{4}-\d{2}-[a-f0-9]{32})$/;
const json=(value:unknown,status=200)=>Response.json(value,{status,headers:{"cache-control":"no-store","referrer-policy":"no-referrer"}});
const error=(code:string,status=400)=>json({error:{code,message:code}},status);
export interface WaitOptions {
  signal:AbortSignal;
  progress?:(seconds:number)=>Promise<void>|void;
}
export function waitLimit(value:unknown, configured?:string):number {
  if(value!==undefined&&(typeof value!=="number"||!Number.isSafeInteger(value)||value<1))
    throw new TypeError("invalid max_segundos");
  const cap=Number(configured??300);
  return Math.min(300,Number.isSafeInteger(cap)&&cap>0?cap:300,Number(value??300));
}
function sleep(ms:number,signal:AbortSignal):Promise<void>{
  return new Promise((resolve,reject)=>{
    if(signal.aborted){reject(signal.reason);return;}
    const abort=()=>{clearTimeout(timer);reject(signal.reason);};
    const timer=setTimeout(()=>{signal.removeEventListener("abort",abort);resolve();},ms);
    signal.addEventListener("abort",abort,{once:true});
  });
}
type Sample={response:Response;done:boolean};
/** Poll at most once per interval; heartbeat can run while the upstream is slow. */
export async function pollWait(
  sample:()=>Promise<Sample>, seconds:number, options:WaitOptions, interval=10_000,
):Promise<Response>{
  const start=Date.now(), deadline=start+seconds*1000;
  let last:Sample|undefined;
  const controller=new AbortController();
  const stop=()=>controller.abort(options.signal.reason);
  options.signal.addEventListener("abort",stop,{once:true});
  if(options.signal.aborted)stop();
  const tick=options.progress ? setInterval(()=>{
    if(!controller.signal.aborted)Promise.resolve(options.progress?.(Math.floor((Date.now()-start)/1000))).catch(()=>controller.abort());
  },15_000):undefined;
  try{
    for(;;){
      controller.signal.throwIfAborted();
      // Bound the wait itself even if an upstream operation stalls.
      const remaining=Math.max(0,deadline-Date.now());
      if(last&&remaining===0)return last.response;
      let timer:ReturnType<typeof setTimeout>|undefined;
      let abort:()=>void=()=>{};
      const bounded=new Promise<undefined>((resolve,reject)=>{
        timer=setTimeout(()=>resolve(undefined),remaining);
        abort=()=>reject(controller.signal.reason);
        controller.signal.addEventListener("abort",abort,{once:true});
      });
      let next:Sample|undefined;
      try{next=await Promise.race([sample(),bounded]);}
      finally{clearTimeout(timer);controller.signal.removeEventListener("abort",abort);}
      if(!next)return last?.response??error("wait_upstream_timeout",503);
      last=next;
      if(next.done)return next.response;
      await sleep(Math.min(interval,Math.max(0,deadline-Date.now())),controller.signal);
    }
  }finally{
    if(tick!==undefined)clearInterval(tick);
    options.signal.removeEventListener("abort",stop);
    controller.abort();
  }
}
type Row=Record<string,unknown>;
const terminal=(row:Row)=>row.status==="registered"||row.status==="rejected";
function mediaRow(row:Row):Row{
  return {media_id:row.media_id,status:row.status,...(row.kind?{kind:row.kind}:{}),
    ...(row.reason?{reason:row.reason}:{}),
    ...(typeof row.low_resolution==="boolean"?{low_resolution:row.low_resolution}:{}),
    ...(typeof row.width==="number"?{width:row.width}:{}),
    ...(typeof row.height==="number"?{height:row.height}:{})};
}
export async function waitVideo(request:Request,env:RestEnv,id:string,seconds:unknown,options:WaitOptions){
  if(!VIDEO.test(id))return error("invalid_video_id");
  let limit:number;
  try{limit=waitLimit(seconds,env.CLIPOTECA_WAIT_MAX_SECONDS);}catch{return error("invalid_wait");}
  const path="/v1/videos/"+id;
  // Existing status can make multiple external requests per sample. Paid Worker
  // is required for the maximum interval; see docs/ESPERA_LARGA.md.
  return pollWait(async()=>{
    const response=await handleVideos(new Request(new URL(path,request.url),{signal:options.signal}),env);
    if(!response?.ok)return {response:response??error("video_not_found",404),done:true};
    const row=await response.json<Row>();
    const done=["done","failed","expired"].includes(String(row.status));
    return {response:json({...row,listo:done,tracking_url:new URL(path+"/ver",request.url).href}),done};
  },limit,options);
}
export async function waitMedia(request:Request,env:RestEnv,input:{session_id?:string;media_ids?:string[];max_segundos?:number},options:WaitOptions){
  const session=input.session_id,ids=input.media_ids;
  if((session===undefined)===(ids===undefined)||
    (session!==undefined&&(typeof session!=="string"||!SESSION.test(session)))||
    (ids!==undefined&&(!Array.isArray(ids)||ids.length<1||ids.length>12||ids.some(x=>typeof x!=="string"||!MEDIA.test(x)))))
    return error("invalid_wait");
  let limit:number;
  try{limit=waitLimit(input.max_segundos,env.CLIPOTECA_WAIT_MAX_SECONDS);}catch{return error("invalid_wait");}
  const settled=new Map<string,Row>();
  const get=(path:string)=>handleMedia(new Request(new URL(path,request.url),{signal:options.signal}),env);
  return pollWait(async()=>{
    let data:Row={};
    if(session){
      const response=await get("/v1/uploads/"+session);
      if(!response?.ok)return {response:response??error("session_not_found",404),done:true};
      data=await response.json<Row>();
    }else{
      const files:Row[]=[];
      for(const id of new Set(ids)){
        options.signal.throwIfAborted();
        let row=settled.get(id);
        if(!row){
          const response=await get("/v1/media/"+id);
          if(!response?.ok)return {response:response??error("media_not_found",404),done:true};
          row=mediaRow(await response.json<Row>());
          if(terminal(row))settled.set(id,row);
        }
        files.push(row);
      }
      data={files};
    }
    const files=data.files as Row[];
    const done=files.length>0&&files.every(terminal);
    return {response:json({...data,listo:done}),done};
  },limit,options);
}
export function trackingPage(id:string):Response{
  if(!VIDEO.test(id))return error("invalid_video_id");
  const nonce=Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,"0")).join("");
  const html='<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Clipoteca - seguimiento</title><h1>Estado del video</h1><pre id="state">Consultando...</pre><video id="video" controls playsinline hidden width="360"></video><p><a id="download" hidden download="video.mp4">Descargar video</a></p><script nonce="'+nonce+'">const api="/v1/videos/'+id+'";const state=document.getElementById("state");async function poll(){try{const r=await fetch(api,{cache:"no-store"});if(!r.ok)throw Error("HTTP "+r.status);const v=await r.json();state.textContent=JSON.stringify({estado:v.status,etapas:v.stages,qa:v.qa},null,2);if(v.status==="done"&&v.file_url){const video=document.getElementById("video"),link=document.getElementById("download");video.src=api+"/file";video.hidden=false;link.href=api+"/file";link.hidden=false;return}if(["failed","expired"].includes(v.status))return}catch(e){state.textContent="No disponible: "+e.message}setTimeout(poll,10000)}poll();</script></html>';
  return new Response(html,{headers:{"content-type":"text/html; charset=utf-8","cache-control":"no-store","referrer-policy":"no-referrer","x-content-type-options":"nosniff","content-security-policy":"default-src 'none'; script-src 'nonce-"+nonce+"'; connect-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'"}});
}
export async function handleWait(request:Request,env:RestEnv):Promise<Response|null>{
  const url=new URL(request.url);
  const video=/^\/v1\/videos\/([^/]+)\/(esperar|ver)$/.exec(url.pathname);
  const media=url.pathname==="/v1/media/esperar";
  if(!video&&!media)return null;
  if(video?.[2]==="ver")return request.method==="GET"?trackingPage(video[1]!):error("method_not_allowed",405);
  if(video?request.method!=="GET":request.method!=="POST")return error("method_not_allowed",405);
  let input:{session_id?:string;media_ids?:string[];max_segundos?:number}={};
  try{
    if(media){
      const raw=await request.text();
      if(raw.length>8192)throw Error();
      input=JSON.parse(raw);
      if(!input||typeof input!=="object"||Array.isArray(input)||Object.keys(input).some(k=>!["session_id","media_ids","max_segundos"].includes(k)))throw Error();
    }else if(url.searchParams.has("max_segundos"))input.max_segundos=Number(url.searchParams.get("max_segundos"));
  }catch{return error("invalid_wait");}
  const run=(options:WaitOptions)=>video?waitVideo(request,env,video[1]!,input.max_segundos,options):waitMedia(request,env,input,options);
  if(!request.headers.get("accept")?.includes("text/event-stream"))return run({signal:request.signal});
  const abort=new AbortController();
  const disconnect=()=>abort.abort();
  request.signal.addEventListener("abort",disconnect,{once:true});
  if(request.signal.aborted)disconnect();
  const enc=new TextEncoder();
  const stream=new ReadableStream<Uint8Array>({
    start(controller){
      const send=(s:string)=>{if(!abort.signal.aborted)controller.enqueue(enc.encode(s));};
      send(": connected\n\n");
      void run({signal:abort.signal,progress:()=>send(": keep-alive\n\n")}).then(async result=>{
        send("event: result\ndata: "+JSON.stringify({http_status:result.status,result:await result.json()})+"\n\n");
        if(!abort.signal.aborted)controller.close();
      }).catch(()=>{if(!abort.signal.aborted)controller.error(new Error("wait failed"));})
        .finally(()=>request.signal.removeEventListener("abort",disconnect));
    },
    cancel(){disconnect();request.signal.removeEventListener("abort",disconnect);},
  });
  return new Response(stream,{headers:{"content-type":"text/event-stream","cache-control":"no-store","referrer-policy":"no-referrer"}});
}
