/** Asynchronous video delivery through GitHub Actions, separate from public clip storage. */
import * as queue from "./upload-control";
import type { RestEnv } from "./rest";
import { handleMedia } from "./media";
import repoAssets from "./repo-assets.json";
import { loadCatalog, type Clip } from "./catalog";
import { quota } from "./quota";
import { validateVideoScript } from "./video-schema";
import { zipEntry } from "./video-zip";
import { ArtifactExpired, artifactResponse, serveVideo } from "./video-stream";

const ID = /^[a-f0-9]{32}$/;
const json = (body: unknown, status = 200) => Response.json(body, {
  status, headers: {"cache-control": "no-store", "referrer-policy": "no-referrer"},
});
const error = (code: string, status: number) => json({error: {code, message:code}}, status);

// Run names and artifact names can be public. Never put the bearer capability in them.
async function workflowId(id: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(id));
  return Array.from(new Uint8Array(digest), b=>b.toString(16).padStart(2,"0")).join("");
}
const object = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

type Run = {id:number; display_title:string; status:string; conclusion:string|null; updated_at?:string};
type Job = {name:string; status:string; conclusion:string|null;
  steps?: {name:string; status:string; conclusion:string|null}[]};
type Artifact = {id:number; name:string; expired:boolean; size_in_bytes:number};
type Context = {base:string; token:string; fetcher:typeof fetch};
type SceneWarning = {scene_id:string;degraded_reason:string};
type Delivery = {qa?:{pass:boolean;failed:string[];warnings?:SceneWarning[]}; clips_indexed?:string[]};
function context(env: RestEnv): Context | null {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  const token = env.CLIPOTECA_DISPATCH_TOKEN;
  return token && /^[\w.-]+\/[\w.-]+$/.test(repo)
    ? {base: "https://api.github.com/repos/" + repo, token,fetcher:env.WAIT_FETCH??((input,init)=>fetch(input,init))} : null;
}
function github(ctx: Context, path: string, init: RequestInit = {}) {
  return ctx.fetcher(ctx.base + path, {...init, redirect:"manual", headers: {
    authorization: "Bearer " + ctx.token, accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28", "user-agent": "clipoteca-worker",
    ...(init.method === "POST" ? {"content-type":"application/json"} : {}),
  }});
}
function authorized(request: Request, token?: string): boolean {
  if (!token || token.length < 16) return false;
  const auth = request.headers.get("authorization") ?? "";
  return auth.startsWith("Bearer ") && auth.length === token.length + 7 &&
    [...token].every((c, i) => c === auth[i + 7]);
}
function marker(id: string): Request {
  return new Request("https://clipoteca.invalid/_video/" + id);
}
async function remember(id: string) {
  try {
    const cache = (caches as CacheStorage & {default: Cache}).default;
    await cache.put(marker(id), new Response("1", {headers:{"cache-control":"public,max-age=600"}}));
  } catch { /* Dispatch already succeeded. */ }
}
async function remembered(id: string) {
  try {
    return Boolean(await (caches as CacheStorage & {default: Cache}).default.match(marker(id)));
  } catch { return false; }
}
async function submit(request: Request, ctx: Context, env: RestEnv) {
  // Public by design: anyone connected by REST/MCP may render, within per-IP and global quotas.
  const owner = authorized(request, env.CLIPOTECA_WRITE_TOKEN);
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return error("invalid_video", 400);
  }
  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > 262144) return error("body_too_large", 413);
  let raw: unknown;
  try {
    const reader = request.body?.getReader();
    if (!reader) return error("invalid_video", 400);
    const decoder = new TextDecoder("utf-8", {fatal:true});
    let text = "", size = 0;
    try {
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.length;
        if (size > 262144) return error("body_too_large", 413);
        text += decoder.decode(part.value, {stream:true});
      }
      text += decoder.decode();
      raw = JSON.parse(text);
    } finally { await reader.cancel().catch(() => {}); }
  } catch { return error("invalid_video", 400); }
  if (!object(raw) || Object.keys(raw).some(k => !["script","index_clips"].includes(k)) ||
      (raw.index_clips !== undefined && typeof raw.index_clips !== "boolean")) {
    return error("invalid_video", 400);
  }
  const details = validateVideoScript(raw.script);
  if (details.length) return json({error:"invalid_script", details}, 400);
  // The schema guarantees this shape; retain explicit narrowing for TypeScript.
  if (!object(raw.script) || !Array.isArray(raw.script.scenes)) return error("invalid_video", 400);
  let catalog: Clip[] | undefined;
  const kinds = new Map<string, "video" | "image">();
  const problem = (code:string, message:string, status=400) => json({error:{code,message}},status);
  for (const scene of raw.script.scenes) {
    if (!object(scene) || scene.media === undefined) continue;
    const media = scene.media;
    if (!object(media) || Object.keys(media).some(k=>!["ref","start","end","motion","framing"].includes(k)) ||
        typeof media.ref !== "string" ||
        !/^(?:u[1-9a-f][a-f0-9]{0,11}|a[0-9]{4}-[0-9]{2}-[a-f0-9]{32}|clip-[0-9]{6}|asset:[a-z0-9][a-z0-9-]{0,63})$/.test(media.ref) ||
        (media.motion!==undefined && (typeof media.motion!=="string" || !["auto","zoom_in","zoom_out","pan_left","pan_right"].includes(media.motion))) ||
        (media.framing!==undefined && (typeof media.framing!=="string" || !["cover","contain"].includes(media.framing))) ||
        [media.start,media.end].some(n=>n!==undefined && (typeof n!=="number" || !Number.isFinite(n) || n<0 || n>120)) ||
        (typeof media.end==="number" && media.end<=Number(media.start??0)) ||
        (scene.on_error!==undefined && !["fail","search"].includes(String(scene.on_error))) ||
        (scene.on_error==="search" && (!object(scene.query) || typeof scene.prompt!=="string"))) {
      return problem("invalid_media_ref","Referencia o ventana multimedia no valida.");
    }
    const ref=media.ref;
    if (!ref.startsWith("asset:") && !ref.startsWith("clip-") && scene.on_error===undefined &&
        object(scene.query) && typeof scene.prompt==="string" && scene.prompt.trim()) {
      scene.on_error="search";
    }
    let kind = kinds.get(ref);
    if (ref.startsWith("asset:")) {
      if (!repoAssets.includes(ref)) return problem("asset_not_found","Asset fuera de lista.",404);
      kind = "image";
    } else if (!kind && ref.startsWith("clip-")) {
      try { catalog ??= await loadCatalog(env); }
      catch { return problem("catalog_unavailable","Catalogo no disponible.",503); }
      if (!catalog.some(c=>c.id===ref && c.status==="published")) {
        return problem("clip_not_found","Clip inexistente o retirado.",404);
      }
      kind = "video";
    } else if (!kind) {
      const response=await handleMedia(new Request(new URL("/v1/media/"+ref,request.url)),env);
      if (!response?.ok) return problem(response?.status===404?"media_not_found":"media_registry_unavailable",
        response?.status===404?"Aporte inexistente.":"Registro de aportes no disponible.",response?.status===404?404:503);
      const record:unknown=await response.json();
      if (!object(record)) return problem("invalid_media_ref","Registro de aporte no valido.");
      if (record.status==="rejected") {
        return problem("media_rejected","El aporte "+ref+" fue rechazado: "+String(record.reason??"sin motivo"),409);
      }
      if (!["registered","uploading","processing"].includes(String(record.status))) {
        return problem("invalid_media_ref","Estado de aporte no valido.");
      }
      // Old upload tickets may not carry kind until staging completes.
      if (record.kind==="video" || record.kind==="image") kind = record.kind;
      else if (record.status==="registered") return problem("invalid_media_ref","Tipo de aporte no valido.");
    }
    if (kind === "image" && (media.start!==undefined || media.end!==undefined)) {
      return problem("invalid_image_window","start/end no aplican a imagenes.");
    }
    if (kind === "video" && (media.motion!==undefined || media.framing!==undefined)) {
      return problem("invalid_video_motion","motion/framing solo para imagenes.");
    }
    if (kind) kinds.set(ref,kind);
  }
  const script = JSON.stringify(raw.script);
  if (script.length > 60_000) return error("body_too_large", 413);
  if (!await quota(request, "video")) return error("rate_limited", 429);
  if (!owner && queue.configured(env) && !await queue.admit(request, env, "limit_video")) {
    return error("rate_limited", 429);
  }
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    b => b.toString(16).padStart(2,"0")).join("");
  const response = await github(ctx, "/actions/workflows/render.yml/dispatches", {
    method:"POST", body: JSON.stringify({ref:"main", inputs:{
      request_id:await workflowId(id), script_json:script, index_clips:String(raw.index_clips ?? true),
    }}),
  });
  if (response.status !== 204) {
    return error("dispatch_unavailable", response.status === 429 ? 429 : 503);
  }
  await remember(id);
  return json({id, status:"queued", status_url:new URL("/v1/videos/" + id, request.url).href}, 202);
}
async function pageRows<T>(ctx: Context, path: string, key: string): Promise<T[]> {
  const all: T[] = [];
  for (let page = 1; page <= 10; page++) {
    const response = await github(ctx, path + (path.includes("?") ? "&" : "?") +
      "per_page=100&page=" + page);
    if (!response.ok) throw new Error("actions unavailable");
    const payload = await response.json() as Record<string, T[]>;
    const rows = payload[key];
    if (!Array.isArray(rows)) throw new Error("invalid actions response");
    all.push(...rows);
    if (rows.length < 100) return all;
  }
  return all;
}
async function findRun(ctx: Context, id: string): Promise<Run | undefined> {
  const runs = await pageRows<Run>(ctx, "/actions/workflows/render.yml/runs?event=workflow_dispatch",
    "workflow_runs");
  return runs.find(r => r.display_title === "render " + id && Number.isSafeInteger(r.id));
}
async function artifacts(ctx: Context, run: Run) {
  return pageRows<Artifact>(ctx, "/actions/runs/" + run.id + "/artifacts", "artifacts");
}
function member(rows: Artifact[], name: string) {
  return rows.find(row => row.name === name && Number.isSafeInteger(row.id));
}
function expired(row?: Artifact) { return row?.expired === true; }
function expiredRun(run: Run) {
  return run.status === "completed" && run.conclusion === "success" &&
    Boolean(run.updated_at && Date.now() - Date.parse(run.updated_at) > 7 * 86400_000);
}
async function signedArtifact(ctx: Context, artifact: Artifact) {
  if (artifact.expired) throw new ArtifactExpired("artifact expired");
  const response = await github(ctx, "/actions/artifacts/" + artifact.id + "/zip");
  if (response.status === 410) throw new ArtifactExpired("artifact expired");
  if (response.status !== 302 && response.status !== 307) return {response};
  const url = new URL(response.headers.get("location") ?? "");
  if (url.protocol !== "https:" || url.username || url.password || url.port ||
      !(url.hostname.endsWith(".blob.core.windows.net") ||
        url.hostname.endsWith(".actions.githubusercontent.com") ||
        url.hostname.endsWith(".githubusercontent.com"))) throw new Error("invalid artifact host");
  return {url:url.href};
}
async function archive(ctx: Context, artifact: Artifact, limit: number): Promise<ArrayBuffer> {
  if (!Number.isFinite(artifact.size_in_bytes) || artifact.size_in_bytes > limit ||
      artifact.size_in_bytes < 1) throw new Error("invalid artifact");
  const signed = await signedArtifact(ctx,artifact);
  const response = signed.url ? await artifactResponse(signed.url,undefined,ctx.fetcher) : signed.response!;
  const length = Number(response.headers.get("content-length") ?? artifact.size_in_bytes);
  if (!response.ok || !Number.isFinite(length) || length > limit) {
    await response.body?.cancel();
    throw new Error("artifact unavailable");
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("missing archive body");
  const chunks:Uint8Array[] = [];
  let size=0;
  try {
    for (;;) {
      const {done,value}=await reader.read();
      if(done) break;
      size+=value.length;
      if(size>limit) throw new Error("artifact exceeds bound");
      chunks.push(value);
    }
  } finally {await reader.cancel().catch(()=>{});}
  const buffer=new Uint8Array(size);
  let offset=0;
  for(const chunk of chunks) {buffer.set(chunk,offset);offset+=chunk.length;}
  return buffer.buffer;
}
function stage(job?: Job) {
  return job ? {status:job.status, conclusion:job.conclusion} :
    {status:"queued", conclusion:null};
}
async function status(ctx: Context, run: Run, id: string, rid: string, rows: Artifact[], request: Request) {
  const jobs = await pageRows<Job>(ctx, "/actions/runs/" + run.id + "/jobs", "jobs");
  const scenes = jobs.filter(job => /^scene(?:\s|\(|$)/.test(job.name));
  const assemble = jobs.find(job => job.name === "assemble");
  const video = member(rows, "video-" + rid);
  const evidence = member(rows, "entrega-" + rid);
  let delivery: Delivery = {};
  const warnings:string[] = [];
  let videoExpired = expired(video);
  if (evidence && !expired(evidence)) {
    try {
      const buffer = await archive(ctx, evidence, 2_000_000);
      const bytes = await zipEntry(buffer, "entrega.json", 1_000_000);
      const data: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (object(data) && object(data.qa) && typeof data.qa.pass === "boolean" &&
          Array.isArray(data.qa.failed) && data.qa.failed.every(x=>typeof x==="string") &&
          Array.isArray(data.clips_indexed) && data.clips_indexed.every(x=>typeof x==="string")) {
        const sceneWarnings = data.warnings;
        if (sceneWarnings !== undefined && (!Array.isArray(sceneWarnings) ||
            !sceneWarnings.every(w=>object(w) && typeof w.scene_id==="string" &&
              typeof w.degraded_reason==="string"))) throw new Error("invalid scene warnings");
        delivery = {qa:{pass:data.qa.pass, failed:data.qa.failed,
          ...(sceneWarnings === undefined ? {} : {warnings:sceneWarnings.map(w=>({
            scene_id:w.scene_id, degraded_reason:w.degraded_reason,
          }))})}, clips_indexed:data.clips_indexed};
      } else throw new Error("invalid delivery evidence");
    } catch {
      warnings.push("delivery_evidence_unavailable");
    }
  }
  const indexed = member(rows, "indexed-clips");
  if (indexed && !expired(indexed)) {
    try {
    const bytes = await zipEntry(await archive(ctx,indexed,1_000_000),"index.json",500_000);
    const data: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (object(data) && Array.isArray(data.indexed) && data.indexed.every(x=>typeof x==="string")) {
      delivery.clips_indexed = data.indexed;
    } else throw new Error("invalid index evidence");
    } catch { warnings.push("index_evidence_unavailable"); }
  }
  if (video && !videoExpired && delivery.qa?.pass !== false) {
    try {
      const signed = await signedArtifact(ctx,video);
      if (signed.response && !signed.response.ok) throw new Error("artifact unavailable");
      await signed.response?.body?.cancel();
    } catch (exc) {
      if (exc instanceof ArtifactExpired) videoExpired = true;
      else warnings.push("video_availability_unconfirmed");
    }
  }
  const file = Boolean(video && !videoExpired && delivery.qa?.pass !== false);
  const state = videoExpired || (!video && expiredRun(run)) ? "expired" :
    file ? "done" : run.status !== "completed" ?
      (["queued","waiting","requested","pending"].includes(run.status) ? "queued" : "running") :
      "failed";
  return json({
    id, status:state, stages:{
      plan:stage(jobs.find(j=>j.name==="plan")), voice:stage(jobs.find(j=>j.name==="voice")),
      music:stage(jobs.find(j=>j.name==="music")),
      scenes:{ready:scenes.filter(j=>j.conclusion==="success").length,total:scenes.length},
      assemble:stage(assemble),
      qa:stage(assemble?.steps?.find(j=>j.name==="Quality control (14 gates)") as Job | undefined),
      index:stage(jobs.find(j=>j.name==="index")),
    },
    ...delivery, ...(warnings.length ? {warnings} : {}), ...(file ? {file_url:new URL("/v1/videos/" + id + "/file",request.url).href} : {}),
  });
}

export async function handleVideos(request: Request, env: RestEnv): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (path !== "/v1/videos" && !path.startsWith("/v1/videos/")) return null;
  const match = /^\/v1\/videos\/([^/]+)(\/file)?$/.exec(path);
  if (path !== "/v1/videos" && (!match || !ID.test(match[1]!))) {
    return error("invalid_video_id", 400);
  }
  if (path === "/v1/videos" ? request.method !== "POST" : request.method !== "GET") {
    const response = error("method_not_allowed", 405);
    response.headers.set("Allow", path === "/v1/videos" ? "POST" : "GET");
    return response;
  }
  const ctx = context(env);
  if (!ctx) return error("actions_unavailable", 503);
  try {
    if (path === "/v1/videos") return await submit(request, ctx, env);
    const id = match![1]!;
    const rid = await workflowId(id);
    const run = await findRun(ctx, rid);
    if (!run) {
      return !match![2] && await remembered(id)
        ? json({id,status:"queued"}) : error("video_not_found", 404);
    }
    const rows = await artifacts(ctx, run);
    if (!match![2]) return await status(ctx, run, id, rid, rows, request);
    const video = member(rows, "video-" + rid);
    if (expired(video) || (!video && expiredRun(run))) return error("video_expired", 410);
    if (!video) return error("video_not_available", 404);
    const signed = await signedArtifact(ctx,video);
    if (!signed.url) {
      await signed.response?.body?.cancel();
      throw new Error("missing signed artifact URL");
    }
    return await serveVideo(signed.url,request.headers.get("range"));
  } catch (exc) {
    if (exc instanceof ArtifactExpired) return error("video_expired",410);
    return error("actions_unavailable", 503);
  }
}
