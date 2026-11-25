/** MCP 2026-07-28 and legacy 2025 Streamable HTTP, one server per request. */
import {
  createMcpHandler, McpServer, hostHeaderValidationResponse, originValidationResponse,
} from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import { handleCatalog } from "./catalog";
import { handleRest, type RestEnv } from "./rest";
import { handleScript } from "./script";
import { APP_VERSION } from "./version";
import { PUBLIC_NOTICE } from "./media";

export interface McpEnv extends RestEnv {
  MCP_ALLOWED_HOSTS?: string;
  MCP_ALLOWED_ORIGINS?: string;
}

const clipSchema = z.object({
  id: z.string().regex(/^clip-\d{6}$/),
  title: z.object({ es: z.string(), en: z.string() }),
  description: z.object({ es: z.string(), en: z.string() }),
  tags: z.object({ es: z.array(z.string()), en: z.array(z.string()) }),
  source: z.object({
    provider: z.string(), source_language: z.string(),
    provider_id: z.string().optional(), source_title: z.string().optional(),
    source_url: z.string().optional(), source_start: z.number().nonnegative().optional(),
    source_end: z.number().positive().optional(), issue_number: z.number().int().optional(),
    issue_author: z.string().optional(), issue_url: z.string().optional(),
  }),
  media: z.object({
    aspect_ratio: z.string(), duration_seconds: z.number(), width: z.number(),
    fps: z.number(), has_audio: z.boolean(),
  }),
  storage: z.object({
    asset_url: z.string(), thumbnail_url: z.string().optional(),
    storyboard_urls: z.array(z.string().url()).max(8).optional(),
  }),
  status: z.string().optional(), created_at: z.string().optional(),
});
const searchOutputSchema = z.object({
  clips: z.array(clipSchema), next_cursor: z.string().nullable(),
  status: z.string().optional(), request_id: z.string().optional(),
  semantic: z.enum(["ready", "pending", "unavailable"]).optional(),
  semantic_reason: z.string().optional(),
  scores: z.record(z.string().regex(/^clip-\d{6}$/), z.number().finite()).optional(),
});
const scriptSceneInput = z.strictObject({
  id:z.string().regex(/^[a-zA-Z0-9_-]{1,64}$/),
  narration:z.string().min(1).max(1500),
  query:z.string().min(1).max(200),
  orientation:z.enum(["9:16","16:9","1:1","other",
    "portrait","vertical","landscape","horizontal","square"]),
  min_duration:z.number().positive().max(20),
});
const scriptSourceSchema = z.object({
  provider:z.string(),source_language:z.string(),
  provider_id:z.string().optional(),source_title:z.string().optional(),
  source_url:z.string().optional(),
  source_start:z.number().nonnegative().optional(),
  source_end:z.number().positive().optional(),
});
const scriptMediaSchema = z.object({
  type:z.literal("video"),clip_id:z.string().regex(/^clip-\d{6}$/),
  url:z.string().url(),
  thumbnail_url:z.string().url().nullable(),
  storyboard_urls:z.array(z.string().url()),
  duration_seconds:z.number().positive(),
  source:scriptSourceSchema,
});
const scriptOutputSchema = z.object({
  script_id:z.string().regex(/^[a-f0-9]{64}$/),
  scenes:z.array(z.object({
    id:z.string(),narration:z.string(),query:z.string(),
    orientation:z.string(),min_duration:z.number(),
    status:z.enum(["matched","queued","missing"]),
    media:scriptMediaSchema.nullable(),
    reason:z.string().optional(),
    semantic_reason:z.string().optional(),
    request_id:z.string().regex(/^[a-f0-9]{64}$/).optional(),
    job:z.object({request_id:z.string(),status:z.string()}).optional(),
  })).min(1).max(50),
  summary:z.object({matched:z.number().int(),queued:z.number().int(),
    missing:z.number().int(),reused:z.number().int()}),
});
const getClipOutputSchema = z.object({ clip: clipSchema });
const downloadOutputSchema = z.object({ url: z.string().url(), clip_id: z.string().optional() });
const storyboardOutputSchema = z.object({
  clip_id: z.string(),
  frames: z.array(z.object({
    url: z.string().url(), index: z.number().int().nonnegative(),
    timestamp_seconds: z.number().nonnegative(),
  })).max(8),
});
const importOutputSchema = z.object({ request_id: z.string(), status: z.string() });
const jobOutputSchema = z.object({
  request_id: z.string(), status: z.string(), attempt: z.number().int().optional(),
  updated_at: z.string().optional(),
  language_counts: z.record(z.string(), z.number()).optional(),
  provider_counts: z.record(z.string(), z.number()).optional(),
  source_errors: z.record(z.string(), z.string()).optional(),
  terms: z.record(z.string(), z.string()).optional(),
  duplicate_ids: z.array(z.string()).optional(),
});
const capabilityLevel = z.enum(["supported", "best_effort", "unavailable"]);
const sourceCapabilities = z.object({ search: capabilityLevel, preview: capabilityLevel, download: capabilityLevel });
const sourcesOutputSchema = z.object({
  sources: z.array(z.object({ provider: z.string(), status: z.string(), capabilities: sourceCapabilities })),
  kind: z.string().optional(),
});
const answer = (result: object) => ({
  content: [{ type: "text" as const, text: JSON.stringify(result) }],
  structuredContent: result,
});
const fail = (message: string) => ({
  isError: true as const,
  content: [{ type: "text" as const, text: message }],
});
type ActionResult = ReturnType<typeof answer> | ReturnType<typeof fail>;

async function restResult(request: Request, env: McpEnv): Promise<ActionResult> {
  const response = await handleRest(request, env) ?? await handleCatalog(request, env);
  if (!response) return fail("Unknown route");
  if (!response.ok) {
    // Only this local media handler produces these safe, fixed diagnostics.
    if (/^\/v1\/(media|uploads)(\/|$)/.test(new URL(request.url).pathname)) {
      try {
        const {error} = await response.json() as {error?: {code?: string; message?: string}};
        if (typeof error?.code === "string" && /^[a-z0-9_]+$/.test(error.code) &&
            typeof error.message === "string") {
          return fail(error.code + ": " + error.message);
        }
      } catch { /* Fall back to the HTTP status. */ }
    }
    return fail("Clipoteca API error " + response.status);
  }
  const result = (await response.json()) as object;
  return answer(result);
}
const base64Json = (data: string): object => {
  const binary = atob(data.replace(/\s+/g, ""));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)) as object;
};

export async function sourceSnapshot(env: McpEnv): Promise<{ sources: {provider: string; status: string; capabilities: {search: string; preview: string; download: string}}[];kind: string }> {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error("invalid repository");
  const response = await fetch(
    "https://raw.githubusercontent.com/" + repo + "/main/data/sources.json",
    { headers: { accept: "application/vnd.github+json",
      "cache-control": "no-cache", "user-agent": "clipoteca-worker" } },
  );
  if (!response.ok) throw new Error("source snapshot unavailable");
  const raw: unknown = await response.json();
  let sources: unknown = raw;
  if (raw && typeof raw === "object" && "encoding" in raw &&
      (raw as {encoding?: unknown}).encoding === "base64") {
    const data = (raw as {content?: unknown}).content;
    if (typeof data !== "string") throw new Error("missing source content");
    sources = base64Json(data);
  }
  if (!Array.isArray(sources)) throw new Error("malformed provider data");
  const rows = sources.map((rawRow: unknown) => {
    if (!rawRow || typeof rawRow !== "object" || Array.isArray(rawRow)) {
      throw new Error("invalid provider row");
    }
    const row = rawRow as Record<string, unknown>;
    const provider = row.name ?? row.provider;
    const status = row.status;
    // Missing capabilities is invalid; do not silently invent "unavailable".
    const parsed = sourceCapabilities.safeParse(row.capabilities);
    if (typeof provider !== "string" || !provider ||
        typeof status !== "string" || !status || !parsed.success) {
      throw new Error("invalid provider row or capabilities");
    }
    return { provider, status, capabilities: parsed.data };
  });
  return { sources: rows, kind: "actions_credential_presence_snapshot" };
}

export function createClipotecaServer(env: McpEnv, inbound: Request): McpServer {
  const server = new McpServer(
    { name: "clipoteca-comunitaria", version: APP_VERSION },
    { capabilities: { tools: {} } },
  );

  const toolRequest = (path: string, method: string, body?: unknown): Request => {
    const headers = new Headers({ accept: "application/json" });
    const ip = inbound.headers.get("cf-connecting-ip");
    if (ip) headers.set("cf-connecting-ip", ip);
    const authorization = inbound.headers.get("authorization");
    if (authorization) headers.set("authorization", authorization);
    if (body !== undefined) headers.set("content-type", "application/json");
    return new Request(new URL(path, inbound.url), {
      method, headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  };
  const clipId = z.string().regex(/^clip-\d{6}$/);
  const searchInput = {
    query: z.string().min(1).max(200),
    aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
    max_duration: z.number().positive().max(20).optional(),
    min_width: z.number().int().positive().optional(),
    fps: z.number().positive().optional(),
    source: z.string().regex(/^[a-z0-9_]+$/).optional(),
    language: z.enum(["es", "en", "zh", "ru"]).optional(),
    has_audio: z.boolean().optional(),
    limit: z.number().int().min(1).max(100).optional(),
    cursor: z.string().regex(/^(0|[1-9][0-9]*)$/).optional(),
  };

  server.registerTool("search_clips", {
    title: "Search clips", description: "Search verified clips; optionally queue an import.",
    inputSchema: z.strictObject({
      ...searchInput, import_if_missing: z.boolean().optional(),
    }),
    outputSchema: searchOutputSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, import_if_missing, ...filters }) => restResult(
    toolRequest("/v1/search", "POST", {
      query, filters: { ...filters, ...(import_if_missing ? { import_if_missing } : {}) },
    }), env));

  server.registerTool("resolve_script", {
    title:"Resolve script scenes to verified video clips",
    description:"Resolve up to 50 storyboard scenes from existing clips and optionally queue imports with shared throttling.",
    inputSchema:z.strictObject({
      scenes:z.array(scriptSceneInput).min(1).max(50),
    }),
    outputSchema:scriptOutputSchema,
    annotations:{readOnlyHint:false,openWorldHint:true},
  },async ({scenes}) => {
    const response=await handleScript(toolRequest("/v1/script","POST",{scenes}),env);
    if(!response.ok)return fail("Clipoteca API error "+response.status);
    return answer(await response.json() as object);
  });

  server.registerTool("get_clip", {
    title: "Get clip", description: "Return catalogue metadata for one verified clip.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: getClipOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id, "GET"), env);
    if (!response?.ok) return fail("Clip unavailable: " + clip_id);
    return answer({ clip: await response.json() });
  });

  server.registerTool("get_storyboard", {
    title: "Inspect video storyboard",
    description: "Return immutable WebP preview frame URLs and timestamps before downloading video.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: storyboardOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(
      toolRequest("/v1/clips/" + clip_id + "/storyboard", "GET"), env,
    );
    if (!response?.ok) return fail("Clip unavailable: " + clip_id);
    return answer(await response.json() as object);
  });

  server.registerTool("download_clip", {
    title: "Download clip URL", description: "Return verified GitHub Releases MP4 URL, not bytes.",
    inputSchema: z.strictObject({ clip_id: clipId }),
    outputSchema: downloadOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ clip_id }) => {
    const response = await handleCatalog(toolRequest("/v1/clips/" + clip_id + "/download", "GET"), env);
    if (response?.status !== 302) return fail("Clip unavailable: " + clip_id);
    const url = response.headers.get("location");
    if (!url || !url.startsWith("https://")) return fail("No verified asset URL");
    return answer({ clip_id, url });
  });

  server.registerTool("search_and_import", {
    title: "Search and import", description: "Queue asynchronous import; requires write token.",
    inputSchema: z.strictObject({
      query: z.string().min(1).max(200),
      aspect_ratio: z.enum(["9:16", "16:9", "1:1", "other"]).optional(),
      max_results: z.number().int().min(1).max(50).optional(),
    }),
    outputSchema: importOutputSchema,
    annotations: { readOnlyHint: false, openWorldHint: true },
  }, async ({ query, aspect_ratio, max_results }) => restResult(
    toolRequest("/v1/import", "POST", {
      query, filters: {
        ...(aspect_ratio ? { aspect_ratio } : {}),
        ...(max_results ? { limit: max_results } : {}),
      },
    }), env));

  server.registerTool("get_job_status", {
    title: "Get job status", description: "Fetch durable job record or a typed not_found.",
    inputSchema: z.strictObject({ request_id: z.string().regex(/^[a-f0-9]{64}$/) }),
    outputSchema: jobOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async ({ request_id }) => restResult(
    toolRequest("/v1/jobs/" + request_id, "GET"), env));


  const mediaMetadata = {
    kind: z.enum(["video", "image"]), title: z.string().min(1).max(120),
    author: z.string().min(1).max(120), source: z.string().min(1).max(500),
    license_note: z.string().min(1).max(500).optional(),
  };
  const mediaId = z.string().regex(/^(?:u[1-9a-f][a-f0-9]{0,11}|a\d{4}-\d{2}-[a-f0-9]{32})$/);
  const mediaRecord = z.object({
    ...mediaMetadata, media_id: mediaId, bytes: z.number().int().positive(),
    mime: z.string(), origin: z.enum(["url", "upload"]), url: z.string().url(),
    created_at: z.string(), status: z.literal("registered"),
    duration_seconds: z.number().positive().max(120).optional(),
  });
  server.registerTool("register_media_url", {
    title: "Registrar un aporte por URL",
    description: "Registra una URL HTTPS directa. " + PUBLIC_NOTICE,
    inputSchema: z.strictObject({...mediaMetadata, url: z.string().url()}),
    outputSchema: mediaRecord,
    annotations: {readOnlyHint:false, openWorldHint:true},
  }, async input => restResult(toolRequest("/v1/media", "POST", input), env));
  server.registerTool("create_media_upload", {
    title: "Crear enlace de subida",
    description: "Ticket de un archivo, 2 h en modo KV. Declara size en bytes. Hasta 95 MiB puedes usar PUT binario; por encima usa partes de 24 MiB y POST complete. No pidas al usuario abrir el enlace si puedes subirlo. " + PUBLIC_NOTICE,
    inputSchema: z.strictObject({...mediaMetadata,size:z.number().int().positive()}),
    outputSchema: z.object({
      media_id:mediaId,status:z.enum(["pending_upload","uploading"]),expires_at:z.string(),
      upload_url:z.string().url(),put_url:z.string().url(),upload_token:z.string(),part_size:z.number().int().optional(),parts:z.number().int().optional(),
    }),
    annotations: {readOnlyHint:false,openWorldHint:true},
  }, async input => restResult(toolRequest("/v1/media/upload", "POST", input), env));
  server.registerTool("get_media_status", {
    title: "Consultar un aporte",
    description: "Consulta si el aporte esta subiendo, procesando, registrado o rechazado. " + PUBLIC_NOTICE,
    inputSchema: z.strictObject({media_id:mediaId}),
    outputSchema: mediaRecord.partial().extend({media_id:mediaId,status:z.enum(["uploading","processing","registered","rejected"]),reason:z.string().optional()}),
    annotations: {readOnlyHint:true,openWorldHint:true},
  }, async ({media_id}) => restResult(toolRequest("/v1/media/"+media_id, "GET"), env));


  const sessionId=z.string().regex(/^[a-f0-9]{64}$/);
  const sessionFile=z.object({media_id:mediaId,kind:z.enum(["image","video"]),status:z.enum(["uploading","processing","registered","rejected"]),reason:z.string().optional()});
  const sessionMeta={session_id:sessionId,title:z.string().min(1).max(120).optional(),author:z.string().min(1).max(120).optional(),
    source:z.string().min(1).max(500).optional(),license_note:z.string().min(1).max(500).optional()};
  const staged=z.object({media_id:mediaId,status:z.literal("processing")});
  server.registerTool("crear_subida",{
    title:"Crear sesion de aportes",
    description:"Una sesion de 24 horas, hasta 12 fotos o videos. Si el host entrega archivos, usa subir_archivo; si tienes shell, usa ticket_subida con size y partes (PUT unico solo hasta 95 MiB); para imagenes de hasta 40 KiB usa subir_imagen_base64. Solo comparte upload_url cuando el host no pueda subir. "+PUBLIC_NOTICE,
    inputSchema:z.strictObject({}),outputSchema:z.object({session_id:sessionId,expires_at:z.string(),max_files:z.number(),upload_url:z.string().url()}),
    annotations:{readOnlyHint:false,openWorldHint:true},
  },async()=>restResult(toolRequest("/v1/uploads","POST",{}),env));
  server.registerTool("ticket_subida",{
    title:"Obtener ticket para subir directamente",
    description:"Declara size en bytes. Hasta 95 MiB puedes usar PUT binario. Mas de 95 MiB SIEMPRE por partes: split -b 24M -d -a 1 ARCHIVO parte-; for p in parte-*; do curl --fail-with-body -H \"Authorization: Bearer $UPLOAD_TOKEN\" --upload-file \"$p\" \"$PUT_URL/parts/${p##*-}\" || exit 1; done; curl --fail-with-body -X POST -H \"Authorization: Bearer $UPLOAD_TOKEN\" \"$PUT_URL/complete\". Video 100 MiB y 120 s; imagen 10 MiB. "+PUBLIC_NOTICE,
    inputSchema:z.strictObject({...sessionMeta,kind:z.enum(["image","video"]),size:z.number().int().positive()}),
    outputSchema:z.object({media_id:mediaId,status:z.literal("uploading"),expires_at:z.string(),upload_url:z.string().url(),put_url:z.string().url(),upload_token:z.string(),part_size:z.number().int(),parts:z.number().int()}),
    annotations:{readOnlyHint:false,openWorldHint:true},
  },async({session_id,...input})=>restResult(toolRequest("/v1/uploads/"+session_id+"/files","POST",input),env));
  server.registerTool("subir_archivo",{
    title:"Aportar archivo del usuario",
    description:"Primera opcion cuando el host MCP entrega un archivo temporal (ChatGPT fileParams). El servidor descarga la URL temporal HTTPS autorizada, valida el contenido y devuelve media_id. No pidas al usuario abrir enlaces. Si el host no entrega archivos o el origen no esta admitido, usa ticket_subida si tienes red; ultimo recurso: upload_url de crear_subida. "+PUBLIC_NOTICE,
    inputSchema:z.strictObject({...sessionMeta,kind:z.enum(["image","video"]).optional(),file:z.strictObject({download_url:z.string().url().max(4096),file_id:z.string().min(1).max(256),mime_type:z.string().optional(),file_name:z.string().optional()})}),
    outputSchema:staged,_meta:{"openai/fileParams":["file"]},
    annotations:{readOnlyHint:false,openWorldHint:true},
  },async({session_id,...input})=>restResult(toolRequest("/v1/uploads/"+session_id+"/files","POST",input),env));
  server.registerTool("subir_imagen_base64",{
    title:"Aportar imagen pequena",
    description:"Solo si el host puede leer los bytes: base64 puro de hasta 40 KiB decodificados, sin prefijo data:. No inventes bytes. "+PUBLIC_NOTICE,
    inputSchema:z.strictObject({...sessionMeta,image_base64:z.string().max(54616)}),outputSchema:staged,
    annotations:{readOnlyHint:false,openWorldHint:true},
  },async({session_id,...input})=>restResult(toolRequest("/v1/uploads/"+session_id+"/files","POST",input),env));
  server.registerTool("estado_subida",{
    title:"Consultar sesion de aportes",
    description:"Devuelve cada media_id, kind, estado y motivo de rechazo. Usa media_id en media.ref solo cuando status sea registered. Consulta otra vez tras un plazo acotado si sigue procesando.",
    inputSchema:z.strictObject({session_id:sessionId}),outputSchema:z.object({session_id:sessionId,expires_at:z.string(),files:z.array(sessionFile)}),
    annotations:{readOnlyHint:true,openWorldHint:true},
  },async({session_id})=>restResult(toolRequest("/v1/uploads/"+session_id,"GET"),env));

  server.registerTool("render_video", {
    title: "Render a video",
    description: PUBLIC_NOTICE + " Renderiza un guion de 3 a 12 escenas, hasta 60 segundos. media.ref admite aportes de video o imagen u... o a..., clips clip-NNNNNN y SVG del repo asset:clipoteca-pipeline. start/end solo para video; imagenes admiten motion (auto, zoom_in, zoom_out, pan_left, pan_right) y framing (cover, contain). Por defecto on_error=fail; search requiere query y prompt. Ejemplo mixto: {\"title\":\"Agua\",\"aspect\":\"9:16\",\"scenes\":[{\"id\":\"intro\",\"type\":\"intro\",\"title\":\"Mar\",\"say\":\"El mar.\",\"media\":{\"ref\":\"u77\"}},{\"id\":\"fact\",\"type\":\"fact\",\"title\":\"Agua\",\"say\":\"El agua fluye.\",\"media\":{\"ref\":\"clip-000001\"}},{\"id\":\"outro\",\"type\":\"outro\",\"title\":\"Lluvia\",\"say\":\"La lluvia cae.\",\"query\":{\"es\":[\"lluvia\"],\"en\":[\"rain\"]},\"prompt\":\"falling rain\"}]}",
    inputSchema: z.strictObject({
      script: z.object({
        title: z.string(), aspect: z.enum(["9:16","16:9","1:1"]),
        scenes: z.array(z.record(z.string(), z.unknown())).min(3).max(12),
      }).passthrough(),
      index_clips: z.boolean().optional(),
    }),
    outputSchema: z.object({
      id: z.string(), status: z.literal("queued"), status_url: z.string(),
    }),
    annotations: {readOnlyHint: false, openWorldHint: true},
  }, async (input) => restResult(toolRequest("/v1/videos", "POST", input), env));

  server.registerTool("get_video_status", {
    title: "Get video status and download URL",
    description: "Check render stages, quality results and the temporary delivery URL (7 days); the Actions run is public.",
    inputSchema: z.strictObject({id: z.string().regex(/^[a-f0-9]{32}$/)}),
    outputSchema: z.object({
      id: z.string(), status: z.enum(["queued","running","done","failed","expired"]),
      stages: z.record(z.string(), z.unknown()).optional(),
      qa: z.object({pass:z.boolean(),failed:z.array(z.string())}).optional(),
      clips_indexed: z.array(z.string()).optional(), file_url: z.string().optional(),
      warnings: z.array(z.string()).optional(),
    }),
    annotations: {readOnlyHint: true, openWorldHint: true},
  }, async ({id}) => restResult(toolRequest("/v1/videos/" + id, "GET"), env));

  server.registerTool("list_sources", {
    title: "List public source statuses",
    description: "Read credential-presence status snapshot without exposing any values.",
    inputSchema: z.strictObject({}),
    outputSchema: sourcesOutputSchema,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, async () => {
    try { return answer(await sourceSnapshot(env)); }
    catch { return fail("Source snapshot unavailable"); }
  });
  return server;
}

export async function handleMcp(request: Request, env: McpEnv): Promise<Response> {
  const ownHost = new URL(request.url).hostname.toLowerCase();
  const explicitlyAllowed = (env.MCP_ALLOWED_HOSTS ?? "").split(",")
    .map(x => x.trim().toLowerCase()).filter(Boolean);
  const hosts = explicitlyAllowed.length ? explicitlyAllowed : [ownHost];
  const origins = (env.MCP_ALLOWED_ORIGINS ?? "").split(",").map(x => {
    const entry = x.trim();
    if (!entry) return "";
    try { return new URL(entry).hostname.toLowerCase(); }
    catch { return entry.toLowerCase(); }
  }).filter(Boolean);
  // Reject a forged Host even when the allowlist falls back to the Worker URL.
  const suppliedHost = request.headers.get("host")?.toLowerCase().split(":")[0];
  if (!explicitlyAllowed.length && suppliedHost && suppliedHost !== ownHost) {
    return Response.json({ error: {code:"invalid_host"} }, {status:403});
  }
  const rejection = hostHeaderValidationResponse(request, hosts) ??
    originValidationResponse(request, origins);
  if (rejection) return rejection;
  const handler = createMcpHandler(() => createClipotecaServer(env, request), {
    legacy: "stateless", maxRequestBodySize: 65536, responseMode: "json",
  });
  return handler.fetch(request);
}
