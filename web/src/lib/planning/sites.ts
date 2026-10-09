/** Site helpers shared by Settings and the Planner (mirror engine/astrodash/planning.py). */

import { BORTLE_BANDS, bortleFromSqm, sqmFromBortle } from "@/lib/snr";
import type { Site } from "./types";

export const SITE_RADIUS_KM = 5;

export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const dp = (lat2 - lat1) * r;
  const dl = (lon2 - lon1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dl / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(a)));
}

export const defaultSite = (sites: Site[]) => sites.find((s) => s.isDefault) ?? sites[0];

export { bortleFromSqm, sqmFromBortle, BORTLE_BANDS };

/** Parses "45.544, -73.538", "45.544 -73.538" or a Google Maps link ("…/@45.544,-73.538,15z" or "?q=45.5,-73.5"). */
export function parseLatLon(text: string): { lat: number; lon: number } | null {
  const t = text.trim();
  const at = /@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/.exec(t) ?? /[?&](?:q|ll|query)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/.exec(t);
  const plain = /^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/.exec(t);
  const m = at ?? plain;
  if (!m) return null;
  const lat = Number(m[1]);
  const lon = Number(m[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

export const formatLatLon = (lat: number, lon: number) =>
  `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? "E" : "W"}`;
