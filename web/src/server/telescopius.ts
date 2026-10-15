import "server-only";

import { engineGet } from "@/lib/engine";
import type { EventsPayload, ListsPayload, ObjectPayload } from "@/lib/telescopius";

/** Telescopius data via the engine, which holds the key and caches every answer; null when the engine is down. */

export async function loadTelescopiusObject(...names: (string | null | undefined)[]): Promise<ObjectPayload | null> {
  const qs = [...new Set(names.filter((n): n is string => !!n?.trim()))].map((n) => `name=${encodeURIComponent(n)}`).join("&");
  if (!qs) return null;
  const res = await engineGet<ObjectPayload>(`/api/telescopius/object?${qs}`);
  return res.ok ? res.data : null;
}

export async function loadTelescopiusLists(): Promise<ListsPayload | null> {
  const res = await engineGet<ListsPayload>("/api/telescopius/lists");
  return res.ok ? res.data : null;
}

export async function loadSkyEvents(siteId: string): Promise<EventsPayload | null> {
  const res = await engineGet<EventsPayload>(`/api/telescopius/events?siteId=${encodeURIComponent(siteId)}`);
  return res.ok ? res.data : null;
}
