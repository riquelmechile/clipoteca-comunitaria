/** GitHub workflow dispatch for a deterministic Clipoteca request ID. */
export interface DispatchEnv {
  CLIPOTECA_DISPATCH_TOKEN?: string;
  CLIPOTECA_GITHUB_REPO?: string;
}
export interface DispatchInput {
  query: string;
  filters: {
    aspect_ratio?: string | null;
    max_duration?: number;
    min_width?: number;
    fps?: number;
    source?: string;
    language?: string;
    has_audio?: boolean;
    limit?: number;
    cursor?: string;
  };
}
export type DispatchResult = { ok: true } |
  { ok: false; code: string; message: string; status: number };

const UNAVAILABLE: DispatchResult = {
  ok: false, code: "dispatch_unavailable",
  message: "GitHub workflow dispatch is unavailable; no job has been queued",
  status: 503,
};

function dispatchInputs(input: DispatchInput, requestId: string): Record<string, string> {
  return {
    query: input.query,
    request_id: requestId,
    aspect_ratio: input.filters.aspect_ratio ?? "",
    max_duration: String(input.filters.max_duration ?? 20),
    max_results: String(Math.min(input.filters.limit ?? 3, 50)),
    filters_json: JSON.stringify(input.filters),
  };
}

export async function dispatchIngest(
  request: Request, env: DispatchEnv, input: DispatchInput, requestId: string,
): Promise<DispatchResult> {
  if (!/^[0-9a-f]{64}$/.test(requestId)) return UNAVAILABLE;
  const token = env.CLIPOTECA_DISPATCH_TOKEN;
  const repo = env.CLIPOTECA_GITHUB_REPO ?? "riquelmechile/clipoteca-comunitaria";
  if (!token || !/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repo)) return UNAVAILABLE;

  let response: Response;
  try {
    response = await fetch(
      "https://api.github.com/repos/" + repo + "/actions/workflows/ingest.yml/dispatches",
      {
        method: "POST",
        headers: {
          authorization: "Bearer " + token,
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28",
          "content-type": "application/json",
          "user-agent": "clipoteca-worker",
        },
        body: JSON.stringify({ ref: "main", inputs: dispatchInputs(input, requestId) }),
      },
    );
  } catch {
    return UNAVAILABLE;
  }

  if (response.status === 204) {
    return { ok: true };
  }
  if (response.status === 429 || response.status === 403 &&
      response.headers.get("x-ratelimit-remaining") === "0") {
    return {
      ok: false, code: "dispatch_rate_limited",
      message: "GitHub dispatch rate limited; retry later", status: 429,
    };
  }
  // Do not include upstream response bodies: they can contain request details.
  return UNAVAILABLE;
}
