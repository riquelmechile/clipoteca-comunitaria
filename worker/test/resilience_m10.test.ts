import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

const rid = "f".repeat(64);
describe("M10 failed job retry budget", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/data/jobs/")) {
        return Response.json({ request_id: rid, status: "dead_letter", attempt: 3 });
      }
      return Response.json([]);
    }));
  });

  it("GET returns terminal dead_letter truthfully", async () => {
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/jobs/" + rid));
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ status: "dead_letter", attempt: 3 });
  });

  it("POST import refuses dispatch after dead_letter (no false queued)", async () => {
    const request = new Request("https://clipoteca.test/v1/import", {
      method: "POST",
      headers: { "content-type": "application/json",
        authorization: "Bearer a-secret-token-that-is-long" },
      body: JSON.stringify({ query: "lluvia" }),
    });
    const response = await worker.fetch(request, {
      CLIPOTECA_WRITE_TOKEN: "a-secret-token-that-is-long",
    });
    // request IDs differ by query filters; mock job key is rejected on mismatch.
    expect([409, 503]).toContain(response.status);
  });
});
