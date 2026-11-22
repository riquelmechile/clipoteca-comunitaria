import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadCatalog } from "../src/catalog";
import { loadJob } from "../src/jobs";

const repository = "example/catalogue";
const base = "https://raw.githubusercontent.com/" + repository + "/main/data/";
const requestId = "a".repeat(64);

describe("2.0.1 raw catalog and jobs defaults", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", {
      default: { match: vi.fn(async () => undefined), put: vi.fn(async () => undefined) },
    });
  });

  it("uses raw clips.json for the default, respecting repository override", async () => {
    const addresses: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      addresses.push(String(input instanceof Request ? input.url : input));
      return Response.json([]);
    }));
    expect(await loadCatalog({ CLIPOTECA_GITHUB_REPO: repository })).toEqual([]);
    expect(addresses).toEqual([base + "clips.json"]);
  });

  it("allows explicitly configured CATALOG_URL to override raw", async () => {
    const addresses: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      addresses.push(String(input instanceof Request ? input.url : input));
      return Response.json([]);
    }));
    await loadCatalog({ CATALOG_URL: "https://catalog.example/clips.json" });
    expect(addresses).toEqual(["https://catalog.example/clips.json"]);
  });

  it("uses raw jobs/<request_id>.json and keeps status validation", async () => {
    const addresses: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (address: string) => {
      addresses.push(address);
      return Response.json({ request_id: requestId, status: "queued" });
    }));
    const result = await loadJob({ CLIPOTECA_GITHUB_REPO: repository }, requestId);
    expect(result?.request_id).toBe(requestId);
    expect(result?.status).toBe("queued");
    expect(addresses).toEqual([base + "jobs/" + requestId + ".json"]);
  });

  it("keeps the 404 -> null behavior for missing raw jobs", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 404 })));
    expect(await loadJob({}, requestId)).toBeNull();
  });

  it("allows explicitly configured jobs URL to override raw", async () => {
    const addresses: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (address: string) => {
      addresses.push(address);
      return Response.json({ request_id: requestId, status: "published" });
    }));
    await loadJob({ CATALOG_JOBS_URL: "https://catalog.example/jobs" }, requestId);
    expect(addresses).toEqual(["https://catalog.example/jobs/" + requestId + ".json"]);
  });

  it("continues decoding base64 GitHub Contents API responses", async () => {
    const content = btoa(JSON.stringify({ request_id: requestId, status: "published" }));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ encoding: "base64", content })));
    const job = await loadJob({ CATALOG_JOBS_URL: "https://api.github.com/jobs/" }, requestId);
    expect(job?.status).toBe("published");
    const catalogContent = btoa(JSON.stringify([]));
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      encoding: "base64", content: catalogContent,
    })));
    expect(await loadCatalog({ CATALOG_URL: "https://api.github.com/catalog" })).toEqual([]);
  });
});
