import { beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";
import { safePublicSourceUrl, validateSubmission } from "../src/contributions";

const token = "secure-test-issue-token";
const body = { title: "Lluvia en una calle", url: "https://videos.example.org/scene.mp4",
  notes: "Lluvia capturada por un colaborador" };
const env = { CLIPOTECA_ISSUE_TOKEN: token };

describe("M9 URL-only contribution", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
  });

  it.each([
    "http://example.org/v", "https://127.0.0.1/v",
    "https://192.168.0.10/v", "https://169.254.169.254/latest",
    "https://localhost/video", "https://metadata.google.internal",
    "https://name:pass@example.org", "https://[::1]/v",
    "https://example.org:8443/v", "https://example.org/v#part",
  ])("rejects unsafe origin %s without network", (url) => {
    expect(() => safePublicSourceUrl(url)).toThrow();
  });

  it("accepts an Issue without approval and rejects unknown input fields", () => {
    expect(validateSubmission(body).title).toBe(body.title);
    expect(() => validateSubmission({ ...body, hidden: "script" })).toThrow();
  });

  it("creates an Issue with only URL metadata, no source fetch and no secrets in output", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, opts?: RequestInit) => {
      requests.push(String(url));
      const sent = JSON.parse(String(opts?.body)) as { title: string; body: string };
      expect(sent.body).toContain(body.url);
      expect(sent.body).toContain("pipeline automatizado");
      expect(sent.body).not.toContain(token);
      return Response.json({
        number: 19, html_url: "https://github.com/riquelmechile/clipoteca-comunitaria/issues/19",
      }, { status: 201 });
    }));
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/contributions", {
      method: "POST", headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.9" },
      body: JSON.stringify(body),
    }), env);
    expect(response.status).toBe(201);
    expect(await response.json()).toMatchObject({ status: "queued", issue_number: 19 });
    expect(requests).toEqual(["https://api.github.com/repos/riquelmechile/clipoteca-comunitaria/issues"]);
  });

  it("never claims Issue accepted on GitHub 403", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 403 })));
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/contributions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }), env);
    expect(response.status).toBe(503);
  });
});
