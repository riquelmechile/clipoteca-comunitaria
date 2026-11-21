/** URL-only, untrusted human contribution intake. No source URL is fetched. */
export interface ContributionEnv {
  CLIPOTECA_ISSUE_TOKEN?: string;
  CLIPOTECA_GITHUB_REPO?: string;
  CLIPOTECA_WRITE_TOKEN?: string;
}
export type Submission = {
  title: string;
  url: string;
  license_confirmed: true;
  notes?: string;
};

const err = (code: string, message: string, status: number) =>
  Response.json({ error: { code, message } }, { status,
    headers: { "cache-control": "no-store" } });

export function safePublicSourceUrl(raw: string): string {
  if (raw.length > 2048 || !/^https:\/\//i.test(raw)) throw new TypeError("HTTPS URL required");
  let url: URL;
  try { url = new URL(raw); } catch { throw new TypeError("invalid URL"); }
  const hostname = url.hostname.toLowerCase();
  if (url.protocol !== "https:" || url.username || url.password ||
      (url.port && url.port !== "443") || url.hash ||
      !hostname || hostname.length > 253 || hostname.includes(":") ||
      /^\d+(\.\d+){3}$/.test(hostname) ||
      /(^|\.)(localhost|local|internal|invalid|test|example|onion)$/.test(hostname) ||
      /^(169\.254|127\.|10\.|192\.168\.|172\.)/.test(hostname) ||
      hostname === "metadata.google.internal") {
    throw new TypeError("public HTTPS URL required");
  }
  return url.href;
}

export function validateSubmission(raw: unknown): Submission {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new TypeError("invalid body");
  const row = raw as Record<string, unknown>;
  if (Object.keys(row).some((key) =>
      !["title", "url", "license_confirmed", "notes"].includes(key))) {
    throw new TypeError("unknown field");
  }
  if (typeof row.title !== "string" || row.title.trim().length < 3 ||
      row.title.length > 120 || /[\r\n\x00-\x1f]/.test(row.title)) {
    throw new TypeError("invalid title");
  }
  if (typeof row.url !== "string") throw new TypeError("invalid URL");
  if (row.license_confirmed !== true) throw new TypeError("license confirmation required");
  if (row.notes !== undefined &&
      (typeof row.notes !== "string" || row.notes.length > 1000 ||
        /[\x00-\x08\x0b\x0e-\x1f]/.test(row.notes))) {
    throw new TypeError("invalid notes");
  }
  return {
    title: row.title.trim(),
    url: safePublicSourceUrl(row.url),
    license_confirmed: true,
    ...(typeof row.notes === "string" ? { notes: row.notes.trim() } : {}),
  };
}

async function limitIntake(request: Request): Promise<boolean> {
  const cache = (caches as CacheStorage & { default: Cache }).default;
  if (!cache) return false;
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ip));
  const key = "https://clipoteca.invalid/_contribution/" +
    [...new Uint8Array(digest)].slice(0, 12).map((n) => n.toString(16).padStart(2, "0")).join("");
  const req = new Request(key);
  if (await cache.match(req)) return false;
  await cache.put(req, new Response("1", {
    headers: { "cache-control": "public, max-age=3600" },
  }));
  return true;
}

function ownerAuthenticated(request: Request, token?: string): boolean {
  if (!token || token.length < 16) return false;
  const raw = request.headers.get("authorization");
  if (!raw?.startsWith("Bearer ")) return false;
  const given = raw.slice(7);
  let difference = given.length ^ token.length;
  for (let i = 0; i < Math.max(given.length, token.length); i++) {
    difference |= (given.charCodeAt(i) || 0) ^ (token.charCodeAt(i) || 0);
  }
  return difference === 0;
}

export async function submitContribution(request: Request, env: ContributionEnv): Promise<Response> {
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!env.CLIPOTECA_ISSUE_TOKEN || !/^[\w.-]+\/[\w.-]+$/.test(repo)) {
    return err("contribution_unavailable", "Contribution intake is not configured", 503);
  }
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return err("invalid_input", "Expected application/json", 400);
  }
  if (Number(request.headers.get("content-length") ?? 0) > 4096) {
    return err("invalid_input", "Contribution too large", 400);
  }
  let submission: Submission;
  try {
    const body = await request.text();
    if (body.length > 4096) throw new TypeError("Contribution too large");
    submission = validateSubmission(JSON.parse(body) as unknown);
  } catch (error) {
    return err("invalid_input", error instanceof Error ? error.message : "Invalid JSON", 400);
  }
  if (!ownerAuthenticated(request, env.CLIPOTECA_WRITE_TOKEN) &&
      !await limitIntake(request)) {
    return err("rate_limited", "One public contribution per hour", 429);
  }

  // Only construct an Issue, never download URL or run commands from it.
  const issueBody = [
    "### URL pública del vídeo", submission.url,
    "### Derechos de uso", "Declaración de autorización del remitente; revisión humana requerida",
    "### Nota del remitente", submission.notes ?? "Sin notas",
    "### Control", "No descargar, redistribuir ni ejecutar esta URL sin verificación independiente",
  ].join("\n\n");
  let upstream: Response;
  try {
    upstream = await fetch("https://api.github.com/repos/" + repo + "/issues", {
      method: "POST",
      headers: {
        authorization: "Bearer " + env.CLIPOTECA_ISSUE_TOKEN,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "clipoteca-worker",
      },
      body: JSON.stringify({
        title: "[Aporte pendiente] " + submission.title,
        body: issueBody,
      }),
    });
  } catch {
    return err("issue_unavailable", "Issue service unavailable", 503);
  }
  if (upstream.status !== 201) {
    return err("issue_unavailable", "Contribution could not be registered", 503);
  }
  let result: unknown;
  try { result = await upstream.json(); } catch { return err("issue_unavailable", "Invalid Issue response", 503); }
  const obj = result as Record<string, unknown>;
  if (typeof obj.number !== "number" || !Number.isSafeInteger(obj.number) ||
      typeof obj.html_url !== "string" ||
      !obj.html_url.startsWith("https://github.com/" + repo + "/issues/")) {
    return err("issue_unavailable", "Invalid Issue receipt", 503);
  }
  return Response.json({
    status: "pending_human_review", issue_number: obj.number, issue_url: obj.html_url,
  }, { status: 201, headers: { "cache-control": "no-store" } });
}
