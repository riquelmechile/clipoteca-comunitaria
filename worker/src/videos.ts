/** Asynchronous video delivery through GitHub Actions, separate from public clip storage. */
import type { RestEnv } from "./rest";
import { quota } from "./quota";
import { MAX_ARCHIVE, zipEntry } from "./video-zip";

const ID = /^[a-f0-9]{32}$/;
const json = (body: unknown, status = 200) => Response.json(body, {
  status, headers: {"cache-control": "no-store", "referrer-policy": "no-referrer"},
});
const error = (code: string, status: number) => json({error: {code}}, status);
const object = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

type Run = {id:number; display_title:string; status:string; conclusion:string|null; updated_at?:string};
type Job = {name:string; status:string; conclusion:string|null;
  steps?: {name:string; status:string; conclusion:string|null}[]};
type Artifact = {id:number; name:string; expired:boolean; size_in_bytes:number};
type Context = {base:string; token:string};
type Delivery = {qa?:{pass:boolean;failed:string[]}; clips_indexed?:string[]};
function context(env: RestEnv): Context | null {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  const token = env.CLIPOTECA_DISPATCH_TOKEN;
  return token && /^[\w.-]+\/[\w.-]+$/.test(repo)
    ? {base: "https://api.github.com/repos/" + repo, token} : null;
}
function github(ctx: Context, path: string, init: RequestInit = {}) {
  return fetch(ctx.base + path, {...init, redirect:"manual", headers: {
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
  if (!authorized(request, env.CLIPOTECA_WRITE_TOKEN)) return error("unauthorized", 401);
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
      !object(raw.script) || !Array.isArray(raw.script.scenes) ||
      raw.script.scenes.length < 3 || raw.script.scenes.length > 12 ||
      typeof raw.script.title !== "string" ||
      !["9:16","16:9","1:1"].includes(String(raw.script.aspect)) ||
      (raw.index_clips !== undefined && typeof raw.index_clips !== "boolean")) {
    return error("invalid_video", 400);
  }
  const script = JSON.stringify(raw.script);
  if (script.length > 60_000) return error("body_too_large", 413);
  if (!await quota(request, "import")) return error("rate_limited", 429);
  const id = Array.from(crypto.getRandomValues(new Uint8Array(16)),
    b => b.toString(16).padStart(2,"0")).join("");
  const response = await github(ctx, "/actions/workflows/render.yml/dispatches", {
    method:"POST", body: JSON.stringify({ref:"main", inputs:{
      request_id:id, script_json:script, index_clips:String(raw.index_clips ?? true),
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
async function archive(ctx: Context, artifact: Artifact, limit: number): Promise<ArrayBuffer> {
  if (artifact.expired || !Number.isFinite(artifact.size_in_bytes) ||
      artifact.size_in_bytes > limit || artifact.size_in_bytes < 1) {
    throw new Error("invalid artifact");
  }
  let response = await github(ctx, "/actions/artifacts/" + artifact.id + "/zip");
  if (response.status === 302 || response.status === 307) {
    const url = new URL(response.headers.get("location") ?? "");
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !(url.hostname.endsWith(".blob.core.windows.net") ||
          url.hostname.endsWith(".actions.githubusercontent.com") ||
          url.hostname.endsWith(".githubusercontent.com"))) throw new Error("invalid artifact host");
    // Never forward GitHub authorization to the signed blob URL.
    response = await fetch(url.href, {redirect:"manual"});
  }
  const length = Number(response.headers.get("content-length") ?? artifact.size_in_bytes);
  if (!response.ok || !Number.isFinite(length) || length > limit) {
    throw new Error("artifact unavailable");
  }
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength > limit) throw new Error("artifact exceeds bound");
  return buffer;
}
function stage(job?: Job) {
  return job ? {status:job.status, conclusion:job.conclusion} :
    {status:"queued", conclusion:null};
}
async function status(ctx: Context, run: Run, id: string, rows: Artifact[], request: Request) {
  const jobs = await pageRows<Job>(ctx, "/actions/runs/" + run.id + "/jobs", "jobs");
  const scenes = jobs.filter(job => /^scene(?:\s|\(|$)/.test(job.name));
  const assemble = jobs.find(job => job.name === "assemble");
  const video = member(rows, "video-" + id);
  const evidence = member(rows, "entrega-" + id);
  let delivery: Delivery = {};
  if (evidence && !expired(evidence)) {
    const buffer = await archive(ctx, evidence, 2_000_000);
    try {
      const bytes = await zipEntry(buffer, "entrega.json", 1_000_000);
      const data: unknown = JSON.parse(new TextDecoder().decode(bytes));
      if (object(data) && object(data.qa) && typeof data.qa.pass === "boolean" &&
          Array.isArray(data.qa.failed) && data.qa.failed.every(x=>typeof x==="string") &&
          Array.isArray(data.clips_indexed) && data.clips_indexed.every(x=>typeof x==="string")) {
        delivery = {qa:{pass:data.qa.pass, failed:data.qa.failed}, clips_indexed:data.clips_indexed};
      } else throw new Error("invalid delivery evidence");
    } catch (exc) {
      // An incomplete assembly may upload reports but not the manifest.
      if (video || !(exc instanceof Error) || exc.message !== "missing zip member") throw exc;
      try {
        const bytes = await zipEntry(buffer, "qa_report.json", 1_000_000);
        const qa: unknown = JSON.parse(new TextDecoder().decode(bytes));
        if (object(qa) && typeof qa.pass === "boolean" && Array.isArray(qa.failed) &&
            qa.failed.every(x=>typeof x==="string")) {
          delivery.qa = {pass:qa.pass, failed:qa.failed};
        }
      } catch { /* No QA report exists when assembly failed earlier. */ }
    }
  }
  const indexed = member(rows, "indexed-clips");
  if (!delivery.clips_indexed && indexed && !expired(indexed)) {
    const bytes = await zipEntry(await archive(ctx,indexed,1_000_000),"index.json",500_000);
    const data: unknown = JSON.parse(new TextDecoder().decode(bytes));
    if (object(data) && Array.isArray(data.indexed) && data.indexed.every(x=>typeof x==="string")) {
      delivery.clips_indexed = data.indexed;
    }
  }
  const file = Boolean(video && !expired(video) && delivery?.qa.pass);
  const state = run.status !== "completed" ?
    (["queued","waiting","requested","pending"].includes(run.status) ? "queued" : "running") :
    file ? "done" : expired(video) || (!video && (expiredRun(run) || expired(evidence)))
      ? "expired" : "failed";
  return json({
    id, status:state, stages:{
      plan:stage(jobs.find(j=>j.name==="plan")), voice:stage(jobs.find(j=>j.name==="voice")),
      music:stage(jobs.find(j=>j.name==="music")),
      scenes:{ready:scenes.filter(j=>j.conclusion==="success").length,total:scenes.length},
      assemble:stage(assemble),
      qa:stage(assemble?.steps?.find(j=>j.name==="Quality control (14 gates)") as Job | undefined),
      index:stage(jobs.find(j=>j.name==="index")),
    },
    ...delivery, ...(file ? {file_url:new URL("/v1/videos/" + id + "/file",request.url).href} : {}),
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
  if (path === "/v1/videos" && !authorized(request, env.CLIPOTECA_WRITE_TOKEN)) {
    return error("unauthorized", 401);
  }
  const ctx = context(env);
  if (!ctx) return error("actions_unavailable", 503);
  try {
    if (path === "/v1/videos") return await submit(request, ctx, env);
    const id = match![1]!;
    const run = await findRun(ctx, id);
    if (!run) {
      return !match![2] && await remembered(id)
        ? json({id,status:"queued"}) : error("video_not_found", 404);
    }
    const rows = await artifacts(ctx, run);
    if (!match![2]) return await status(ctx, run, id, rows, request);
    const video = member(rows, "video-" + id);
    if (expired(video) || (!video && expiredRun(run))) return error("video_expired", 410);
    if (!video) return error("video_not_available", 404);
    const bytes = await zipEntry(await archive(ctx, video, MAX_ARCHIVE), "video.mp4", 100_000_000);
    return new Response(bytes, {headers:{
      "content-type":"video/mp4", "content-length":String(bytes.length),
      "cache-control":"no-store", "referrer-policy":"no-referrer",
      "content-disposition":'attachment; filename="video.mp4"',
    }});
  } catch { return error("actions_unavailable", 503); }
}
