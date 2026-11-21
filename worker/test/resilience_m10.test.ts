import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { requestId } from "../src/jobs";

const rid = "f".repeat(64);
let activeId = rid;
describe("M10 failed job retry budget", () => {
  beforeEach(() => {
    activeId = rid;
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/data/jobs/")) {
        return Response.json({ request_id: activeId, status: "dead_letter", attempt: 3 });
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
    activeId = await requestId("lluvia", { aspect_ratio: null, max_duration: 20 });
    const request = new Request("https://clipoteca.test/v1/import", {
      method: "POST",
      headers: { "content-type": "application/json",
        authorization: "Bearer a-secret-token-that-is-long" },
      body: JSON.stringify({ query: "lluvia" }),
    });
    const response = await worker.fetch(request, {
      CLIPOTECA_WRITE_TOKEN: "a-secret-token-that-is-long",
    });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: { code: "dead_letter" } });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });
});
