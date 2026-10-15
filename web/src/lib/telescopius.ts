/**
 * Telescopius data as the engine serves it (astrodash/telescopius.py): RA/Dec in
 * degrees, sizes in arcminutes, event times in ISO 8601 UTC. The key stays in the engine.
 */

import { isoDate } from "@/lib/planning/night";

export type TelescopiusObject = {
  id: string;
  name: string;
  ids: string[];
  names: string[];
  types: string[];
  ra: number | null;
  dec: number | null;
  majArcmin: number | null;
  minArcmin: number | null;
  paDeg: number | null;
  vmag: number | null;
  photoMag: number | null;
  surfBr: number | null;
  con: string | null;
  conName: string | null;
  morph: string | null;
  url: string | null;
  imageUrl: string | null;
  thumbUrl: string | null;
};

export type ObjectPayload = { keySet: boolean; object: TelescopiusObject | null; matchedName: string | null; error: string | null };

export type ListTarget = {
  id: string;
  name: string;
  ra: number;
  dec: number;
  sizeArcmin: number | null;
  paDeg: number | null;
  vmag: number | null;
  con: string | null;
  thumbUrl: string | null;
  notes: string | null;
};

export type TargetList = { id: string; name: string; url: string | null; targets: ListTarget[] };

export type ListsPayload = { keySet: boolean; lists: TargetList[]; stale?: boolean; error: string | null };

export type SkyEvent = {
  type: string;
  name: string;
  t: string;
  objects: string[];
  deg: number | null;
  visibleLocally: boolean | null;
  regions: string | null;
  rating: string | null;
  zhr: number | null;
  moonIllum: number | null;
  activeWindow: string | null;
  parent: string | null;
};

export type EventsPayload = { keySet: boolean; siteId: string; events: SkyEvent[]; stale?: boolean; error: string | null };

/** The night an event belongs to (the date of the evening), in local time: before noon counts as the previous night. */
export const eventNight = (e: SkyEvent) => isoDate(new Date(Date.parse(e.t) - 12 * 3600e3));

/** Already on the Planning pages (moon phases), or nothing to see (orbital milestones). Unknown types are kept. */
const HIDDEN_TYPES = new Set(["MOON_PHASE", "MOON_APOGEE", "MOON_PERIGEE", "PLANET_INFERIOR_CONJUNCTION", "PLANET_PERIHELION", "PLANET_APHELION"]);

/** Events worth a line: occultations seen from elsewhere and the hidden types are dropped. */
export const relevantEvents = (events: SkyEvent[]) => events.filter((e) => e.visibleLocally !== false && !HIDDEN_TYPES.has(e.type));

const TYPE_LABEL: Record<string, string> = {
  METEOR_SHOWER: "Meteor shower",
  PLANET_OCCULTATION: "Occultation",
  STAR_OCCULTATION: "Occultation",
  TWO_OBJECTS_CONJUNCTION: "Conjunction",
  ECLIPSE: "Eclipse",
  LUNAR_ECLIPSE: "Lunar eclipse",
  SOLAR_ECLIPSE: "Solar eclipse",
  EQUINOX: "Equinox",
  SOLSTICE: "Solstice",
};

export const eventKind = (e: SkyEvent) =>
  TYPE_LABEL[e.type] ?? e.type.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/** One line of detail: ZHR and moon for showers, separation for conjunctions. */
export function eventDetail(e: SkyEvent): string {
  const parts: string[] = [];
  if (e.zhr != null) parts.push(`ZHR ${e.zhr}`);
  if (e.moonIllum != null) parts.push(`moon ${Math.round(e.moonIllum)} %`);
  if (e.rating) parts.push(e.rating.toLowerCase());
  if (e.deg != null && e.type !== "METEOR_SHOWER") parts.push(`${e.deg}° apart`);
  if (e.activeWindow) parts.push(`active ${e.activeWindow.replace(" to ", " → ")}`);
  return parts.join(" · ");
}
