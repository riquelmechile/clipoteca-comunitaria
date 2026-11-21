/** Shared best-effort per-client quota used by imports and query embeddings. */

export async function quota(request: Request, scope: string): Promise<boolean> {
  try {
    const cache = (caches as CacheStorage & { default: Cache }).default;
    if (!cache) return false;
    const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
    const bytes = new TextEncoder().encode(scope + ":" + ip);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const id = [...new Uint8Array(hash)].slice(0, 12)
      .map(n => n.toString(16).padStart(2, "0")).join("");
    const key = new Request("https://clipoteca.invalid/_quota/" + scope + "/" + id);
    if (await cache.match(key)) return false;
    await cache.put(key, new Response("1", {
      headers: {"cache-control": "public, max-age=15"},
    }));
    return true;
  } catch { return false; }
}
