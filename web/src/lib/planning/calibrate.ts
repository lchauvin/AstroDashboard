/**
 * Calibrating the sky model from your own subs (PLAN §6.4, "fixing the model rather than overriding it").
 *
 * The engine measures the sky of every analysed light in e⁻/s/px by photon
 * transfer. Here each measurement is compared with what the model predicts for
 * that frame — the site's SQM through that filter, plus the moonlight at that
 * moment — and the median ratio becomes the filter's κ at that site. κ > 1
 * means your sky is brighter in that band than a flat-spectrum SQM predicts
 * (light pollution lines, airglow); for luminance it is effectively a measured SQM.
 *
 * Only frames in astronomical darkness with the target above 30° are used, and
 * frames with the moon down are preferred, so the moon model's own error stays out.
 */

import { Body, Equator, Horizon, Illumination, Observer } from "astronomy-engine";

import { angularSeparation } from "@/lib/astro/angles";
import type { FilterLetter, PlanRig } from "./rig";
import { FILTER_LETTERS } from "./rig";
import { filterSky, moonlightNl, skyRate } from "./skymodel";
import type { Site, SkySample } from "./types";
import { distanceKm, SITE_RADIUS_KM } from "./sites";

export type FilterCalibration = {
  letter: FilterLetter;
  kappa: number;
  frames: number;
  moonDownFrames: number;
  nights: number;
  /** For broadband filters: the SQM that would reproduce the measured sky with κ = 1. */
  sqmEquivalent: number | null;
  /** Median measured sky, e⁻/s/px (moonless frames when available). */
  measuredRate: number;
};

export type SiteCalibration = {
  siteId: string;
  filters: Partial<Record<FilterLetter, FilterCalibration>>;
  gainEPerAdu: number | null;
  frames: number;
};

const median = (xs: number[]) => {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

type Prepared = SkySample & { letter: FilterLetter; moonDown: boolean; moonNl: { rayleigh: number; mie: number } | null };

const prepCache = new Map<string, Prepared[]>();

/** Sun/moon geometry per sample; cached by payload identity (it does not change between page loads). */
function prepare(samples: SkySample[]): Prepared[] {
  const key = `${samples.length}|${samples[0]?.time}|${samples[samples.length - 1]?.time}`;
  const hit = prepCache.get(key);
  if (hit) return hit;
  const out: Prepared[] = [];
  for (const s of samples) {
    if (!s.time || s.ra == null || s.dec == null || (s.altitude ?? 0) < 30) continue;
    if (!(FILTER_LETTERS as readonly string[]).includes(s.filter)) continue;
    const when = new Date(s.time);
    const obs = new Observer(s.lat, s.lon, 0);
    const sun = Equator(Body.Sun, when, obs, true, true);
    if (Horizon(when, obs, sun.ra, sun.dec, "normal").altitude > -18) continue;
    const moonEq = Equator(Body.Moon, when, obs, true, true);
    const moonAlt = Horizon(when, obs, moonEq.ra, moonEq.dec, "normal").altitude;
    const moonNl =
      moonAlt > 0
        ? moonlightNl({
            illumination: Illumination(Body.Moon, when).phase_fraction,
            altitudeDeg: moonAlt,
            separationDeg: angularSeparation(s.ra, s.dec, moonEq.ra * 15, moonEq.dec),
            targetAltitudeDeg: s.altitude ?? 60,
          })
        : null;
    out.push({ ...s, letter: s.filter as FilterLetter, moonDown: moonAlt <= 0, moonNl });
  }
  prepCache.clear();
  prepCache.set(key, out);
  return out;
}

export function calibrateSites(samples: SkySample[], sites: Site[], rig: PlanRig): Record<string, SiteCalibration> {
  const prepared = prepare(samples);
  const out: Record<string, SiteCalibration> = {};
  for (const site of sites) {
    const here = prepared.filter((s) => distanceKm(s.lat, s.lon, site.lat, site.lon) <= SITE_RADIUS_KM);
    const filters: SiteCalibration["filters"] = {};
    for (const letter of FILTER_LETTERS) {
      const spec = rig.filters[letter];
      if (!spec) continue;
      const rows = here.filter((s) => s.letter === letter);
      if (rows.length < 5) continue;
      const down = rows.filter((s) => s.moonDown);
      const use = down.length >= 10 ? down : rows;
      const base = filterSky(rig, spec, site.sqm, 1);
      const ratios = use
        .map((s) => (s.skyRate - (s.moonNl ? skyRate({ ...base, darkRate: 0 }, s.moonNl) : 0)) / base.darkRate)
        .filter((r) => Number.isFinite(r) && r > 0);
      if (!ratios.length) continue;
      const kappa = median(ratios);
      filters[letter] = {
        letter,
        kappa,
        frames: rows.length,
        moonDownFrames: down.length,
        nights: new Set(use.map((s) => `${s.revisionId}/${s.session}`)).size,
        sqmEquivalent: spec.line ? null : site.sqm - 2.5 * Math.log10(kappa),
        measuredRate: median(use.map((s) => s.skyRate)),
      };
    }
    const broadGain = here.filter((s) => ["L", "R", "G", "B"].includes(s.letter)).map((s) => s.gainEPerAdu);
    out[site.id] = { siteId: site.id, filters, gainEPerAdu: broadGain.length ? median(broadGain) : null, frames: here.length };
  }
  return out;
}
