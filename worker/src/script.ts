/** M23 scene script resolver. Uses the existing search and import controls. */
import { loadCatalog, type Clip } from "./catalog";
import { canonicalJson } from "./jobs";
import { submitImport, type RestEnv } from "./rest";
import { semanticSearch, sha256 } from "./semantic";

export const MAX_SCRIPT_SCENES = 50;
const MAX_SCRIPT_BYTES = 65_536;
const MAX_NARRATION = 1_500;
const MAX_QUERY = 200;
const MAX_ID = 64;
const ASPECT: Record<string, string> = {
  "9:16": "9:16", portrait: "9:16", vertical: "9:16",
  "16:9": "16:9", landscape: "16:9", horizontal: "16:9",
  "1:1": "1:1", square: "1:1", other: "other",
};
const VIDEO_EXTENSIONS = new Set([".mp4", ".m4v", ".mov", ".webm", ".m3u8"]);
const statuses = new Set(["queued", "searching", "fetching", "processing", "publishing"]);

export type ScriptScene = {
  id: string; narration: string; query: string;
  orientation: string; min_duration: number;
};
export type ResolvedMedia = {
  type: "video";
  clip_id: string;
  url: string;
  thumbnail_url: string | null;
  storyboard_urls: string[];
  duration_seconds: number;
  source: {
    provider: string; source_language: string; provider_id?: string;
    source_title?: string; source_url?: string;
    source_start?: number; source_end?: number;
  };
};
export type ResolvedScene = ScriptScene & {
  status: "matched" | "queued" | "missing";
  media: ResolvedMedia | null;
  reason?: string;
  request_id?: string;
  job?: { request_id: string; status: string };
};
export type ScriptResolution = {
  script_id: string;
  scenes: ResolvedScene[];
  summary: {matched: number; queued: number; missing: number};
};

function plain(value: unknown): value is Record<string,unknown> {
  return value !== null && typeof value === "object" &&
    !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}
function limitedString(value: unknown, max: number, label: string): string {
  if (typeof value !== "string" || value.length > max || !value.trim() ||
      /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new TypeError("invalid " + label);
  }
  return value.trim();
}
export function parseScript(value: unknown): ScriptScene[] {
  if (!plain(value) || Object.keys(value).some(k => k !== "scenes") ||
      !Array.isArray(value.scenes) || value.scenes.length < 1 ||
      value.scenes.length > MAX_SCRIPT_SCENES) {
    throw new TypeError("scenes must contain 1..50 entries");
  }
  const seen = new Set<string>();
  return value.scenes.map((entry, index): ScriptScene => {
    if (!plain(entry) || Object.keys(entry).some(k =>
      !["id","narration","query","orientation","min_duration"].includes(k)) ||
      Object.keys(entry).length !== 5) {
      throw new TypeError("invalid scene fields at index " + index);
    }
    const id = limitedString(entry.id, MAX_ID, "scene id");
    if (!/^[a-zA-Z0-9_-]{1,64}$/.test(id) || seen.has(id)) {
      throw new TypeError("scene ids must be unique ASCII identifiers");
    }
    seen.add(id);
    const narration = limitedString(entry.narration, MAX_NARRATION, "narration");
    const query = limitedString(entry.query, MAX_QUERY, "query");
    if (typeof entry.orientation !== "string" ||
        !Object.prototype.hasOwnProperty.call(ASPECT, entry.orientation)) {
      throw new TypeError("invalid orientation");
    }
    const duration = entry.min_duration;
    if (typeof duration !== "number" || !Number.isFinite(duration) ||
        duration <= 0 || duration > 20) {
      throw new TypeError("min_duration must be in (0,20]");
    }
    return {id,narration,query,orientation:entry.orientation,min_duration:duration};
  });
}
function isVideo(clip: Clip): boolean {
  try {
    const url = new URL(clip.storage.asset_url);
    if (url.protocol !== "https:") return false;
    const path = url.pathname.toLowerCase();
    return [...VIDEO_EXTENSIONS].some(ext => path.endsWith(ext));
  } catch { return false; }
}
function mediaFrom(clip: Clip): ResolvedMedia {
  const source = clip.source as ResolvedMedia["source"];
  return {
    type:"video", clip_id:clip.id, url:clip.storage.asset_url,
    thumbnail_url:clip.storage.thumbnail_url ?? null,
    storyboard_urls:clip.storage.storyboard_urls ?? [],
    duration_seconds:clip.media.duration_seconds,
    source: {
      provider: source.provider, source_language: source.source_language,
      ...(source.provider_id ? {provider_id:source.provider_id} : {}),
      ...(source.source_title ? {source_title:source.source_title} : {}),
      ...(source.source_url ? {source_url:source.source_url} : {}),
      ...(source.source_start !== undefined ? {source_start:source.source_start} : {}),
      ...(source.source_end !== undefined ? {source_end:source.source_end} : {}),
    },
  };
}
const responseJson = (body:unknown,status=200) =>
  Response.json(body,{status,headers:{"cache-control":"no-store"}});

export async function resolveScript(
  request: Request, env: RestEnv, scenes: ScriptScene[],
): Promise<ScriptResolution> {
  const script_id = await sha256(canonicalJson({version:1,scenes}));
  // A single catalogue fetch for the entire script; the same snapshot is
  // used for every scene to keep assignments deterministic.
  let catalogue: Clip[];
  try { catalogue = await loadCatalog(env); }
  catch {
    return {
      script_id,
      scenes:scenes.map(scene=>({...scene,status:"missing",media:null,
        reason:"catalogue_unavailable"})),
      summary:{matched:0,queued:0,missing:scenes.length},
    };
  }
  const candidates: Clip[][] = [];
  const reasons: string[] = [];
  for (const scene of scenes) {
    try {
      const params = new URLSearchParams({
        aspect_ratio: ASPECT[scene.orientation]!, min_duration:String(scene.min_duration),
        limit:"100", cursor:"0",
      });
      const page = await semanticSearch(request,env,scene.query,params,false,catalogue);
      candidates.push(page.clips.filter(isVideo));
      reasons.push(page.clips.length && !page.clips.some(isVideo)
        ? "non_video_material" : "no_material");
    } catch {
      candidates.push([]);
      reasons.push("search_unavailable");
    }
  }
  // Maximum bipartite matching: if a later scene needs a clip used by an
  // earlier scene, reassign the earlier one to an alternative when possible.
  const owners = new Map<string,number>();
  const assigned = new Map<number,Clip>();
  function assign(sceneIndex:number,visited:Set<string>):boolean {
    for (const clip of candidates[sceneIndex]!) {
      if (visited.has(clip.id)) continue;
      visited.add(clip.id);
      const owner = owners.get(clip.id);
      if (owner === undefined || assign(owner,visited)) {
        owners.set(clip.id,sceneIndex);
        assigned.set(sceneIndex,clip);
        return true;
      }
    }
    return false;
  }
  for (let i=0;i<scenes.length;i++) assign(i,new Set());
  const output:ResolvedScene[]=[];
  for(let i=0;i<scenes.length;i++){
    const scene=scenes[i]!;
    const chosen=assigned.get(i) ?? candidates[i]?.[0];
    if(chosen){
      output.push({...scene,status:"matched",media:mediaFrom(chosen),
        ...(!assigned.has(i)?{reason:"reuse_unavoidable"}:{})});
      continue;
    }
    if(reasons[i]==="search_unavailable"){
      output.push({...scene,status:"missing",media:null,reason:"search_unavailable"});
      continue;
    }
    try {
      // The ONLY path to an import is the existing submitImport, sharing its
      // deterministic request_id, jobs, quotas, anti-abuse and throttling.
      const result=await submitImport(request,env,{
        query:scene.query,filters:{
          aspect_ratio:ASPECT[scene.orientation]!,limit:3,
        },
      });
      if(!result.ok){
        const problem=await result.json() as {error?:{code?:string}};
        const cause=problem.error?.code ?? "import_unavailable";
        output.push({...scene,status:"missing",media:null,
          reason:reasons[i]==="non_video_material"?"non_video_material:"+cause:cause});
        continue;
      }
      const payload=await result.json() as {
        request_id?:string; status?:string; job?:{request_id?:string;status?:string};
      };
      const rid=payload.request_id;
      const state=payload.status??"unavailable";
      if(rid && /^[a-f0-9]{64}$/.test(rid) && statuses.has(state)){
        output.push({...scene,status:"queued",media:null,request_id:rid,
          job:{request_id:rid,status:state},
          ...(reasons[i]==="non_video_material"?{reason:"non_video_material"}:{})});
      }else{
        output.push({...scene,status:"missing",media:null,
          reason:state==="no_results"?"no_results":state==="dead_letter"?"dead_letter":
            state==="published"?"published_no_material":(reasons[i]??"no_material"),
          ...(rid ? {request_id:rid}:{})});
      }
    } catch {
      output.push({...scene,status:"missing",media:null,reason:"import_unavailable"});
    }
  }
  const summary={matched:output.filter(s=>s.status==="matched").length,
    queued:output.filter(s=>s.status==="queued").length,
    missing:output.filter(s=>s.status==="missing").length};
  return {script_id,scenes:output,summary};
}

export async function handleScript(request:Request,env:RestEnv):Promise<Response>{
  try{
    if (!request.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      throw new TypeError("content-type must be application/json");
    }
    const declared=Number(request.headers.get("content-length")??0);
    if(declared>MAX_SCRIPT_BYTES)throw new TypeError("script exceeds 64 KiB");
    // Bound streaming when Content-Length is absent.
    const reader=request.body?.getReader();
    if(!reader)throw new TypeError("missing body");
    const chunks:Uint8Array[]=[];
    let total=0;
    try{
      while(true){
        const {done,value}=await reader.read();
        if(done)break;
        if(value){
          total+=value.length;
          if(total>MAX_SCRIPT_BYTES){
            await reader.cancel();throw new TypeError("script exceeds 64 KiB");
          }
          chunks.push(value);
        }
      }
    }finally{try{reader.releaseLock();}catch{/* cancelled */}}
    const bytes=new Uint8Array(total);
    let offset=0;
    for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    let parsed:unknown;
    try{parsed=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes));}
    catch{throw new TypeError("invalid JSON");}
    return responseJson(await resolveScript(request,env,parseScript(parsed)));
  }catch(error){
    return responseJson({error:{code:"invalid_script",
      message:error instanceof TypeError?error.message:"Invalid script"}},400);
  }
}
