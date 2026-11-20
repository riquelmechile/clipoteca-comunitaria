import { describe, expect, it } from "vitest";
import worker from "../src/index";

describe("health route", () => {
  it("returns typed service health", async () => {
    const response = await worker.fetch(new Request("https://clipoteca.test/v1/health"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok", service: "clipoteca", version: "0.1.0" });
  });

  it("returns typed 404", async () => {
    const response = await worker.fetch(new Request("https://clipoteca.test/nope"));
    expect(response.status).toBe(404);
  });
});

