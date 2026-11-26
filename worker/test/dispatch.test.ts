import { beforeEach, describe, expect, it, vi } from "vitest";
import { dispatchIngest } from "../src/dispatch";
import { fakeSecret } from "./fake-secrets";

const req = new Request("https://clipoteca.test/v1/import", { method: "POST" });
const requestId = "a".repeat(64);
const input = { query: "lluvia", filters: { aspect_ratio: "9:16", max_duration: 20, limit: 2 } };
const dispatchToken = fakeSecret("dispatch");
const env = {
  CLIPOTECA_DISPATCH_TOKEN: dispatchToken,
  CLIPOTECA_GITHUB_REPO: "riquelmechile/clipoteca-comunitaria",
};

describe("M8 dispatch via official GitHub workflow API", () => {
  let cache: Map<string, Response>;
  beforeEach(() => {
    cache = new Map();
    vi.stubGlobal("caches", { default: {
      match: vi.fn(async (request: Request) => cache.get(request.url)),
      put: vi.fn(async (request: Request, response: Response) => {
        cache.set(request.url, response);
      }),
    } });
  });

  it("sends bounded workflow dispatch, retrying without a lossy cache", async () => {
    const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
      const body = JSON.parse(String(options?.body)) as { inputs: Record<string, string>; ref: string };
      expect(body.ref).toBe("main");
      expect(body.inputs).toMatchObject({ query: "lluvia", request_id: requestId,
        aspect_ratio: "9:16", max_duration: "20", max_results: "2", filters_json: JSON.stringify(input.filters) });
      expect(options?.headers).toMatchObject({ authorization: "Bearer " + dispatchToken });
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetcher);
    expect(await dispatchIngest(req, env, input, requestId)).toEqual({ ok: true });
    expect(await dispatchIngest(req, env, input, requestId)).toEqual({ ok: true });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each([403, 404, 422, 500])("never reports queued when GitHub returns %i", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("untrusted upstream", { status })));
    const result = await dispatchIngest(req, env, input, requestId);
    expect(result).toMatchObject({ ok: false, status: 503 });
    expect(JSON.stringify(result)).not.toContain("untrusted upstream");
  });

  it("maps GitHub 429 into typed quota error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 429 })));
    expect(await dispatchIngest(req, env, input, requestId)).toMatchObject({
      code: "dispatch_rate_limited", status: 429,
    });
  });

  it("never makes network request without a valid token or repo", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect(await dispatchIngest(req, {}, input, requestId)).toMatchObject({ ok: false });
    expect(await dispatchIngest(req, {
      ...env, CLIPOTECA_GITHUB_REPO: "evil/repo/extra",
    }, input, requestId)).toMatchObject({ ok: false });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
