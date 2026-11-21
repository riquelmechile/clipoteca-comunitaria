import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { requestId } from "../src/jobs";
import fixtures from "../../tests/fixtures/request_id_vectors.json";

const video = {
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
    }), { CLIPOTECA_WRITE_TOKEN: "a-secret-token-that-is-long" });
    expect(denied.status).toBe(401);
  });

  it("returns 202 only after accepting GitHub dispatch, job unknown returns 404", async () => {
    const req = new Request("https://api.test/v1/search", {
      method: "POST", headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.7" },
      body: JSON.stringify({ query: "unseen footage", filters: { import_if_missing: true } }),
    });
    const response = await worker.fetch(req, { CLIPOTECA_DISPATCH_TOKEN: "test-secret" });
    // Local catalogue contains only an unrelated rain clip.
    expect(response.status).toBe(202);
    const result = (await response.json()) as { request_id: string };
    expect(result.request_id).toMatch(/^[a-f0-9]{64}$/);
    const job = await worker.fetch(new Request("https://api.test/v1/jobs/" + result.request_id));
    expect(job.status).toBe(404);
    expect(await job.json()).toMatchObject({ error: { code: "job_not_found" } });
  });

  it("never returns 202 when dispatch fails", async () => {
    const request = new Request("https://api.test/v1/import", {
      method: "POST", headers: {
        "content-type": "application/json", authorization: "Bearer a-secret-token-that-is-long",
      }, body: JSON.stringify({ query: "unseen footage" }),
    });
    const response = await worker.fetch(request, {
      CLIPOTECA_WRITE_TOKEN: "a-secret-token-that-is-long",
    });
    expect(response.status).toBe(503);
  });
});
