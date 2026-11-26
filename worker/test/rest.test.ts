import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { requestId } from "../src/jobs";
import fixtures from "../../tests/fixtures/request_id_vectors.json";
import { fakeSecret } from "./fake-secrets";

const writeToken = fakeSecret("write");
const dispatchToken = fakeSecret("dispatch");

const video = {
  status: "published",
  id: "clip-000003", title: { es: "Lluvia en la calle", en: "Rain in the street" },
  description: { es: "Lluvia", en: "Rain" }, tags: { es: ["lluvia"], en: ["rain"] },
  source: { provider: "pexels", source_language: "en" },
  media: { aspect_ratio: "9:16", duration_seconds: 18, width: 1080, fps: 30, has_audio: false },
  storage: { asset_url: "https://github.com/example/releases/download/batch/clip_000003.mp4" },
};

describe("M7 REST contract", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const address = String(input instanceof Request ? input.url : input);
      if (address.includes("/data/jobs/")) return new Response(null, { status: 404 });
      if (address.includes("/dispatches")) return new Response(null, { status: 204 });
      if (init?.method === "POST") return new Response(null, { status: 204 });
      return Response.json([video]);
    }));
  });

  it("matches all Python request id vectors", async () => {
    for (const fixture of fixtures) {
      expect(await requestId(fixture.query, fixture.filters, fixture.horizon))
        .toBe(fixture.request_id);
    }
  });

  it("finds actual published rain 9:16 from catalog", async () => {
    const response = await worker.fetch(new Request("https://api.test/v1/search", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: "lluvia", filters: { aspect_ratio: "9:16" } }),
    }));
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ clips: [{ id: "clip-000003" }] });
  });

  it("validates invalid input and restricted imports", async () => {
    const body = JSON.stringify({ query: "lluvia", filters: { max_duration: 30 } });
    const bad = await worker.fetch(new Request("https://api.test/v1/search", {
      method: "POST", headers: { "content-type": "application/json" }, body,
    }));
    expect(bad.status).toBe(400);
    const denied = await worker.fetch(new Request("https://api.test/v1/import", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: "lluvia" }),
    }), { CLIPOTECA_WRITE_TOKEN: writeToken });
    expect(denied.status).toBe(401);
  });

  it("returns 202 only after accepting GitHub dispatch, job unknown returns 404", async () => {
    const req = new Request("https://api.test/v1/search", {
      method: "POST", headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.7" },
      body: JSON.stringify({ query: "unseen footage", filters: { import_if_missing: true } }),
    });
    const response = await worker.fetch(req, { CLIPOTECA_DISPATCH_TOKEN: dispatchToken });
    // Local catalogue contains only an unrelated rain clip.
    expect(response.status).toBe(202);
    const result = (await response.json()) as { request_id: string };
    expect(result.request_id).toMatch(/^[a-f0-9]{64}$/);
    const job = await worker.fetch(new Request("https://api.test/v1/jobs/" + result.request_id));
    expect(job.status).toBe(404);
    expect(await job.json()).toMatchObject({ error: { code: "job_not_found" } });
  });

  it("uses the Python canonical filters on the real import-to-dispatch path", async () => {
    const examples = fixtures.filter((entry) => "worker_import_filters" in entry);
    expect(examples).toHaveLength(2);
    for (const example of examples) {
      const ip = example.query === "nieve" ? "203.0.113.7" : "203.0.113.8";
      const response = await worker.fetch(new Request("https://api.test/v1/import", {
        method: "POST",
        headers: {
          "content-type": "application/json", "cf-connecting-ip": ip,
          authorization: `Bearer ${writeToken}`,
        },
        body: JSON.stringify({ query: example.query, filters: example.worker_import_filters }),
      }), {
        CLIPOTECA_WRITE_TOKEN: writeToken,
        CLIPOTECA_DISPATCH_TOKEN: dispatchToken,
      });
      expect(response.status).toBe(202);
      expect(await response.json()).toMatchObject({ request_id: example.request_id });
      const calls = vi.mocked(fetch).mock.calls.filter(([url]) =>
        String(url instanceof Request ? url.url : url).includes("/dispatches"));
      const [, options] = calls.at(-1)!;
      const body = JSON.parse(String(options?.body)) as { inputs: Record<string, string> };
      expect(body.inputs.request_id).toBe(example.request_id);
      expect(body.inputs.filters_json).toBeDefined();
      expect(JSON.parse(body.inputs.filters_json!)).toEqual(example.filters);
    }
  });

  it("returns idempotent 202 before per-IP quota for the same request within 15s", async () => {
    const stored = new Map<string, Response>();
    vi.stubGlobal("caches", { default: {
      match: vi.fn(async (key: Request) => stored.get(key.url)),
      put: vi.fn(async (key: Request, value: Response) => { stored.set(key.url, value); }),
    } });
    const makeRequest = (query: string) => new Request("https://api.test/v1/import", {
      method: "POST",
      headers: { "content-type": "application/json",
        authorization: `Bearer ${writeToken}`,
        "cf-connecting-ip": "198.51.100.7" },
      body: JSON.stringify({ query, filters: { aspect_ratio: "16:9" } }),
    });
    const env = {
      CLIPOTECA_WRITE_TOKEN: writeToken,
      CLIPOTECA_DISPATCH_TOKEN: dispatchToken,
    };
    const first = await worker.fetch(makeRequest("nieve"), env);
    const second = await worker.fetch(makeRequest("nieve"), env);
    expect(first.status).toBe(202);
    expect(second.status).toBe(202);
    expect(await first.json()).toEqual(await second.json());
    const dispatched = vi.mocked(fetch).mock.calls.filter(([url]) =>
      String(url instanceof Request ? url.url : url).includes("/dispatches"));
    expect(dispatched).toHaveLength(1);
    const different = await worker.fetch(makeRequest("lluvia"), env);
    expect(different.status).toBe(429);
    expect(await different.json()).toMatchObject({ error: { code: "rate_limited" } });
  });

  it("names unknown body fields and points to nested filters", async () => {
    const bad = await worker.fetch(new Request("https://api.test/v1/search", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ query: "lluvia", aspect_ratio: "16:9" }),
    }));
    expect(bad.status).toBe(400);
    const payload = await bad.json() as { error: { message: string } };
    expect(payload.error.message).toContain("aspect_ratio");
    expect(payload.error.message).toContain("filters");
    expect(payload.error.message).not.toContain("query must be nonempty");
  });

  it("never returns 202 when dispatch fails", async () => {
    const request = new Request("https://api.test/v1/import", {
      method: "POST", headers: {
        "content-type": "application/json", authorization: `Bearer ${writeToken}`,
      }, body: JSON.stringify({ query: "unseen footage" }),
    });
    const response = await worker.fetch(request, {
      CLIPOTECA_WRITE_TOKEN: writeToken,
    });
    expect(response.status).toBe(503);
  });
});
