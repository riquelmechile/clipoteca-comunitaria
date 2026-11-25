/** Shared fail-closed best-effort per-IP quota for imports/query embeddings.

An isolate-resident synchronous reservation is acquired before the first await
so concurrent requests sharing the same IP + scope cannot all pass the Cache
API read-before-write check.
*/

/** Accepted video requests per IP and UTC day. */
export const VIDEO_DAILY_LIMIT = 50;

const inFlight = new Set<string>();
export function resetQuotaReservations(): void { inFlight.clear(); }

export async function quota(request: Request, scope: string, mediaBytes = 0): Promise<boolean> {
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
    if (scope !== "media" && await cache.match(key)) return false;
    if (scope === "media") {
      const day = new Date().toISOString().slice(0, 10);
      const daily = new Request(key.url + "/" + day);
      const previous = await cache.match(daily);
      const current = previous ? await previous.json() as {count:number; bytes:number} : {count:0,bytes:0};
      if (!Number.isSafeInteger(mediaBytes) || mediaBytes <= 0 ||
          !Number.isSafeInteger(current.count) || !Number.isSafeInteger(current.bytes) ||
          current.count < 0 || current.bytes < 0 || current.count >= 20 ||
          current.bytes + mediaBytes > 200 * 1024 * 1024) return false;
      const ttl = Math.max(1, Math.ceil((Date.parse(day + "T00:00:00Z") + 86400000 - Date.now()) / 1000));
      await cache.put(daily, new Response(JSON.stringify({count:current.count+1,bytes:current.bytes+mediaBytes}), {
        headers:{"cache-control":"public,max-age="+ttl},
      }));
      return true;
    }
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
