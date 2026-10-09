import "server-only";

/** Server-side access to the engine (FastAPI, :3007). The browser reaches it through the /engine rewrite. */
export const ENGINE_URL = process.env.ASTRODASH_ENGINE_URL ?? "http://127.0.0.1:3007";

export type EngineResult<T> =
  | { ok: true; data: T }
  | { ok: false; reason: "offline" | "scanning" | "not-found" | "error"; message: string };

export async function engineGet<T>(path: string): Promise<EngineResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${ENGINE_URL}${path}`, { cache: "no-store" });
  } catch {
    return { ok: false, reason: "offline", message: `The engine is not reachable at ${ENGINE_URL}.` };
  }
  if (res.ok) return { ok: true, data: (await res.json()) as T };
  if (res.status === 503) return { ok: false, reason: "scanning", message: "The first scan of the data root is still running." };
  if (res.status === 404) return { ok: false, reason: "not-found", message: "Not found." };
  return { ok: false, reason: "error", message: `Engine error ${res.status}.` };
}

/** POST/PUT a JSON body to the engine (server side). */
export async function engineSend<T>(path: string, body: unknown, method: "POST" | "PUT" = "POST"): Promise<EngineResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${ENGINE_URL}${path}`, {
      method,
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return { ok: false, reason: "offline", message: `The engine is not reachable at ${ENGINE_URL}.` };
  }
  if (res.ok) return { ok: true, data: (await res.json()) as T };
  if (res.status === 503) return { ok: false, reason: "scanning", message: "The first scan of the data root is still running." };
  if (res.status === 404) return { ok: false, reason: "not-found", message: "Not found." };
  return { ok: false, reason: "error", message: `Engine error ${res.status}.` };
}
