/** Shared fail-closed best-effort per-IP quota for imports/query embeddings.

An isolate-resident synchronous reservation is acquired before the first await
so concurrent requests sharing the same IP + scope cannot all pass the Cache
API read-before-write check.
*/

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
    await cache.put(key, new Response("1", {
      headers: {"cache-control": "public, max-age=15"},
    }));
    return true;
  } catch {
    return false;
  } finally {
    inFlight.delete(reservation);
  }
}
