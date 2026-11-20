import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleCatalog, normalizeSearch, searchTokens } from "../src/catalog";

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
  it("normalizes compatibility text and includes original source title", async () => {
    expect(normalizeSearch("ＲＡＩＮ")).toBe("rain");
    expect(normalizeSearch("VíDÉO")).toBe("video");
    expect(searchTokens("下雨暴雨")).toContain("下雨");
    const withOriginal = {
      ...clip,
      title: { es: "Escena", en: "Scene" },
      description: { es: "Paisaje", en: "Landscape" },
      tags: { es: [], en: [] },
      source: { ...clip.source, source_title: "Дождь en ＲＡＩＮ", keywords: ["Noche"] },
    };
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([withOriginal])));
    for (const query of ["rain", "дождь", "NOCHE", "rain%20ночь"]) {
      const response = await handleCatalog(new Request(`https://api.test/v1/clips?q=${query}`), {});
      const body = (await response?.json()) as { items: unknown[] };
      if (query.includes("ночь")) expect(body.items).toHaveLength(0);
      else expect(body.items).toHaveLength(1);
    }
  });

  it("treats multiple words as independent tokens", async () => {
    const response = await handleCatalog(
      new Request("https://api.test/v1/clips?q=rain%20street"), {},
    );
    const body = (await response?.json()) as { items: unknown[] };
    expect(body.items).toHaveLength(1);
  });

  it("rejects invalid pagination before network", async () => {
    const requestPaths = [
      "?limit=NaN", "?limit=0", "?limit=101", "?limit=-1",
      "?cursor=-1", "?cursor=Infinity", "?cursor=1.5",
    ];
    for (const suffix of requestPaths) {
      const response = await handleCatalog(new Request(`https://api.test/v1/clips${suffix}`), {});
      expect(response?.status).toBe(400);
      expect(await response?.json()).toMatchObject({ error: { code: "invalid_pagination" } });
    }
  });

  it("uses the wrangler TTL configuration", async () => {
    const put = vi.fn();
    vi.stubGlobal("caches", { default: { match: vi.fn(async () => undefined), put } });
    await handleCatalog(new Request("https://api.test/v1/clips"), {
      CATALOG_TTL_SECONDS: "5",
    });
    const cached = put.mock.calls.at(0)?.at(1) as Response | undefined;
    expect(cached?.headers.get("cache-control")).toBe("public, max-age=5");
  });

});
