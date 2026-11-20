import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleCatalog } from "../src/catalog";

const clip = {
  id: "clip-000001",
  title: { es: "Lluvia", en: "Rain" },
  description: { es: "Calle", en: "Street" },
  tags: { es: ["lluvia"], en: ["rain"] },
  source: { provider: "pexels", source_language: "en" },
  media: { aspect_ratio: "9:16", duration_seconds: 10, width: 1080, fps: 30, has_audio: false },
  storage: { asset_url: "https://github.com/example/releases/download/batch/clip_000001.mp4" },
};

describe("catalog routes", () => {
  beforeEach(() => {
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put: vi.fn() } });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([clip])));
  });

  it("filters the public list", async () => {
    const response = await handleCatalog(
      new Request("https://api.test/v1/clips?q=lluvia&aspect_ratio=9%3A16"),
      { CATALOG_URL: "https://catalog.test/clips.json" },
    );
    expect(response?.status).toBe(200);
    const payload = (await response?.json()) as { items: unknown[] };
    expect(payload.items).toHaveLength(1);
  });

  it("returns typed 404 and redirects downloads", async () => {
    const missing = await handleCatalog(new Request("https://api.test/v1/clips/clip-000002"), {});
    expect(missing?.status).toBe(404);
    const download = await handleCatalog(
      new Request("https://api.test/v1/clips/clip-000001/download"),
      {},
    );
    expect(download?.status).toBe(302);
    expect(download?.headers.get("location")).toBe(clip.storage.asset_url);
  });
});
