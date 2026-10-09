/** Time series for one target through one night, for the altitude chart (full astronomy-engine precision). */

import { Body, Equator, Horizon, Observer } from "astronomy-engine";

import { horizonAt, localNoon, type NightGrid } from "./night";
import type { Site } from "./types";

export type SeriesPoint = {
  t: number;
  alt: number;
  az: number;
  moonAlt: number;
  sunAlt: number;
  /** Minimum usable altitude at the target's azimuth (floor + horizon profile). */
  floor: number;
};

export function nightSeries(site: Site, grid: NightGrid, ra: number, dec: number, stepMinutes = 10): SeriesPoint[] {
  const obs = new Observer(site.lat, site.lon, site.elevation ?? 0);
  // Sunset-ish to sunrise-ish: an hour either side of darkness, or local 18:00–06:00 when there is none.
  const noon = localNoon(grid.date, site.lon).getTime();
  const from = grid.dusk != null ? grid.dusk - 3_600_000 : noon + 6 * 3_600_000;
  const to = grid.dawn != null ? grid.dawn + 3_600_000 : noon + 18 * 3_600_000;
  const out: SeriesPoint[] = [];
  for (let t = from; t <= to; t += stepMinutes * 60_000) {
    const when = new Date(t);
    const h = Horizon(when, obs, ra / 15, dec, "normal");
    const moon = Equator(Body.Moon, when, obs, true, true);
    const sun = Equator(Body.Sun, when, obs, true, true);
    out.push({
      t,
      alt: h.altitude,
      az: h.azimuth,
      moonAlt: Horizon(when, obs, moon.ra, moon.dec, "normal").altitude,
      sunAlt: Horizon(when, obs, sun.ra, sun.dec, "normal").altitude,
      floor: horizonAt(site, h.azimuth),
    });
  }
  return out;
}
