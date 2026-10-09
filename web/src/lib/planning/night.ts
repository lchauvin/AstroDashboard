/**
 * The fast night evaluator behind the ranking, the calendar and the season strips.
 *
 * The sun, the moon and the sidereal time come from astronomy-engine once per
 * night and site (`nightGrid`); each target is then a few lines of spherical
 * trigonometry per sample (`evaluateTarget`). That is what makes hundreds of
 * targets × 90 nights affordable on every page load.
 *
 * Approximations, all small next to the minimum-altitude floor they feed:
 * catalog J2000 positions are not precessed (≤ 0.4° by 2026) and target
 * refraction is ignored (≤ 0.05° above 20°). The vendored `ephemeris.ts` is the
 * reference, and `scripts/verify-planner.ts` pins this module against it.
 */

import { Body, Equator, Horizon, Illumination, MoonPhase, Observer, SearchAltitude, SiderealTime } from "astronomy-engine";

import { angularSeparation } from "@/lib/astro/angles";
import type { HorizonPoint, Site } from "./types";

const DEG = Math.PI / 180;

export type GridSample = {
  t: number; // ms epoch
  /** Local sidereal time, degrees. */
  lst: number;
  moonRa: number;
  moonDec: number;
  moonAlt: number;
};

export type NightGrid = {
  /** "YYYY-MM-DD" of the evening the night starts. */
  date: string;
  site: { lat: number; lon: number };
  dusk: number | null;
  dawn: number | null;
  darkHours: number;
  stepMinutes: number;
  /** Samples at the centre of each step inside astronomical darkness. */
  samples: GridSample[];
  /** Moon illuminated fraction at the middle of the night. */
  moonIllum: number;
  /** Moon phase angle at mid-night: 0 new, 90 first quarter, 180 full, 270 last quarter. */
  moonPhase: number;
  /** Hours of darkness with the moon below the horizon. */
  moonFreeHours: number;
};

const observer = (site: Site | { lat: number; lon: number; elevation?: number | null }) =>
  new Observer(site.lat, site.lon, ("elevation" in site && site.elevation) || 0);

/** UTC instant of local noon (by longitude, not by time zone) on the given calendar date. */
export function localNoon(date: string, lon: number): Date {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d, 12, 0, 0) - (lon / 15) * 3_600_000);
}

/** `withMoon: false` skips the moon (season strips only need altitudes) — moon fields are then NaN. */
export function nightGrid(site: Site, date: string, stepMinutes = 10, withMoon = true): NightGrid {
  const obs = observer(site);
  const noon = localNoon(date, site.lon);
  const dusk = SearchAltitude(Body.Sun, obs, -1, noon, 1, -18);
  const dawn = dusk ? SearchAltitude(Body.Sun, obs, +1, dusk.date, 1, -18) : null;
  const mid = dusk && dawn ? new Date((dusk.date.getTime() + dawn.date.getTime()) / 2) : new Date(noon.getTime() + 12 * 3_600_000);
  const moonIllum = Illumination(Body.Moon, mid).phase_fraction;
  const moonPhase = MoonPhase(mid);
  if (!dusk || !dawn) {
    return { date, site: { lat: site.lat, lon: site.lon }, dusk: null, dawn: null, darkHours: 0, stepMinutes, samples: [], moonIllum, moonPhase, moonFreeHours: 0 };
  }
  const step = stepMinutes * 60_000;
  const t0 = dusk.date.getTime();
  const t1 = dawn.date.getTime();
  const samples: GridSample[] = [];
  for (let t = t0 + step / 2; t < t1; t += step) {
    const when = new Date(t);
    const lst = (((SiderealTime(when) * 15 + site.lon) % 360) + 360) % 360;
    if (!withMoon) {
      samples.push({ t, lst, moonRa: NaN, moonDec: NaN, moonAlt: NaN });
      continue;
    }
    const eq = Equator(Body.Moon, when, obs, true, true);
    const hor = Horizon(when, obs, eq.ra, eq.dec, "normal");
    samples.push({ t, lst, moonRa: eq.ra * 15, moonDec: eq.dec, moonAlt: hor.altitude });
  }
  const darkHours = (t1 - t0) / 3_600_000;
  const moonFree = samples.filter((s) => s.moonAlt <= 0).length * (stepMinutes / 60);
  return { date, site: { lat: site.lat, lon: site.lon }, dusk: t0, dawn: t1, darkHours, stepMinutes, samples, moonIllum, moonPhase, moonFreeHours: Math.min(darkHours, moonFree) };
}

/** Altitude and azimuth (N = 0°, E = 90°) from hour angle, declination and latitude, degrees. */
export function altAz(lstDeg: number, ra: number, dec: number, lat: number): { alt: number; az: number } {
  const h = (lstDeg - ra) * DEG;
  const d = dec * DEG;
  const p = lat * DEG;
  const sinAlt = Math.sin(p) * Math.sin(d) + Math.cos(p) * Math.cos(d) * Math.cos(h);
  const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
  const az = Math.atan2(-Math.cos(d) * Math.sin(h), Math.sin(d) * Math.cos(p) - Math.cos(d) * Math.cos(h) * Math.sin(p));
  return { alt: alt / DEG, az: ((az / DEG) % 360 + 360) % 360 };
}

/** Minimum usable altitude at an azimuth: the site floor, or the horizon profile where higher. */
export function horizonAt(site: Pick<Site, "minAlt" | "horizon">, az: number): number {
  const prof = site.horizon;
  if (!prof?.length) return site.minAlt;
  if (prof.length === 1) return Math.max(site.minAlt, prof[0].alt);
  let lo: HorizonPoint = prof[prof.length - 1];
  let hi: HorizonPoint = prof[0];
  for (let i = 0; i < prof.length; i++) {
    if (prof[i].az >= az) {
      hi = prof[i];
      lo = i > 0 ? prof[i - 1] : prof[prof.length - 1];
      break;
    }
    if (i === prof.length - 1) {
      lo = prof[i];
      hi = prof[0];
    }
  }
  const span = ((hi.az - lo.az) % 360 + 360) % 360 || 360;
  const off = ((az - lo.az) % 360 + 360) % 360;
  const alt = lo.alt + ((hi.alt - lo.alt) * off) / span;
  return Math.max(site.minAlt, alt);
}

export type TargetSample = {
  t: number;
  alt: number;
  az: number;
  moonAlt: number;
  moonSep: number;
};

/** The usable samples of one night for one target (dark, and above the horizon mask). */
export function usableSamples(grid: NightGrid, site: Pick<Site, "minAlt" | "horizon">, ra: number, dec: number): TargetSample[] {
  const out: TargetSample[] = [];
  for (const s of grid.samples) {
    const { alt, az } = altAz(s.lst, ra, dec, grid.site.lat);
    if (alt < site.minAlt || alt < horizonAt(site, az)) continue;
    out.push({ t: s.t, alt, az, moonAlt: s.moonAlt, moonSep: s.moonAlt > 0 ? angularSeparation(ra, dec, s.moonRa, s.moonDec) : 180 });
  }
  return out;
}

/** Usable hours only (no moon work), for season strips and 30/60/90-day sums. */
export function usableHours(grid: NightGrid, site: Pick<Site, "minAlt" | "horizon">, ra: number, dec: number): number {
  let n = 0;
  for (const s of grid.samples) {
    const { alt, az } = altAz(s.lst, ra, dec, grid.site.lat);
    if (alt >= site.minAlt && alt >= horizonAt(site, az)) n++;
  }
  return (n * grid.stepMinutes) / 60;
}

/** Peak altitude reached during darkness (for "never gets high enough" filtering). */
export function maxDarkAltitude(grid: NightGrid, ra: number, dec: number): number {
  let best = -90;
  for (const s of grid.samples) best = Math.max(best, altAz(s.lst, ra, dec, grid.site.lat).alt);
  return best;
}

/** "YYYY-MM-DD" for today's evening in the browser/server local calendar, shifted by n days. */
export function isoDate(base: Date, addDays = 0): string {
  const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + addDays);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Night grids for a run of evenings, memoised per site + date + step (they do not change). */
const gridCache = new Map<string, NightGrid>();
export function grids(site: Site, start: string, days: number, stepMinutes = 10, withMoon = true): NightGrid[] {
  const out: NightGrid[] = [];
  const [y, m, d] = start.split("-").map(Number);
  for (let i = 0; i < days; i++) {
    const date = isoDate(new Date(y, m - 1, d), i);
    const key = `${site.lat.toFixed(4)}|${site.lon.toFixed(4)}|${date}|${stepMinutes}|${withMoon}`;
    let g = gridCache.get(key);
    if (!g) {
      g = nightGrid(site, date, stepMinutes, withMoon);
      if (gridCache.size > 4000) gridCache.clear();
      gridCache.set(key, g);
    }
    out.push(g);
  }
  return out;
}
