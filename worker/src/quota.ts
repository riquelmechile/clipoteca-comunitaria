/** Shared fail-closed best-effort per-IP quota for imports/query embeddings.

An isolate-resident synchronous reservation is acquired before the first await
so concurrent requests sharing the same IP + scope cannot all pass the Cache
API read-before-write check.
*/

/** Accepted video requests per IP and UTC day. */
export const VIDEO_DAILY_LIMIT = 50;

const inFlight = new Set<string>();
export function resetQuotaReservations(): void { inFlight.clear(); }

export async function quota(request: Request, scope: string): Promise<boolean> {
  const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
  const reservation = scope + ":" + ip;
  if (inFlight.has(reservation)) return false;
  inFlight.add(reservation);
  try {
    const cache = (caches as CacheStorage & { default: Cache }).default;
    if (!cache) return false;
    const bytes = new TextEncoder().encode(reservation);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    const id = [...new Uint8Array(hash)].slice(0, 12)
      .map(n => n.toString(16).padStart(2, "0")).join("");
    const key = new Request("https://clipoteca.invalid/_quota/" + scope + "/" + id);
    if (await cache.match(key)) return false;
    if (scope === "video") {
      const now = new Date();
      const day = now.toISOString().slice(0, 10);
      const daily = new Request(key.url + "/" + day);
      const prior = await cache.match(daily);
      const count = prior ? Number(await prior.clone().text()) : 0;
      if (!Number.isInteger(count) || count < 0 || count >= VIDEO_DAILY_LIMIT) return false;
      const midnight = Date.parse(day + "T00:00:00Z") + 86400000;
      const ttl = Math.max(1, Math.ceil((midnight - now.getTime()) / 1000));
      await cache.put(daily, new Response(String(count + 1), {
        headers: {"cache-control": "public, max-age=" + ttl},
      }));
    }
    await cache.put(key, new Response("1", {
      headers: {"cache-control": "public, max-age=" + (scope === "video" ? 300 : 15)},
    }));
    return true;
  } catch {
    return false;
  } finally {
    inFlight.delete(reservation);
  }
}
