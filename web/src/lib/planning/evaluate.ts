/**
 * One target, one night, every filter: how much the moon costs each filter.
 *
 * No target brightness is assumed. A faint target is sky-limited, so an hour
 * under a sky twice as bright is worth about half a moonless hour:
 * `efficiency` = mean over the night of (moonless sky ÷ tonight's sky), from
 * the sky model calibrated on your own subs. That is the "per-filter loss vs.
 * a moonless night" of PLAN §6.2, and why OIII and B lose more to the moon
 * than Hα and SII (moonlight is a blue continuum, a narrow line filter passes
 * little of it).
 */

import type { FilterLetter } from "./rig";
import { moonlightNl, skyRate, type FilterSky } from "./skymodel";
import type { TargetSample } from "./night";

export type FilterNight = {
  letter: FilterLetter;
  hours: number;
  /** 0–1: what tonight's hours are worth against the same hours with no moon. */
  efficiency: number;
  /** Mean sky rate over the usable samples, e⁻/s/px. */
  meanSky: number;
};

export function evaluateNight(
  samples: TargetSample[],
  stepMinutes: number,
  moonIllum: number,
  letters: FilterLetter[],
  skies: Partial<Record<FilterLetter, FilterSky>>,
): FilterNight[] {
  const moon = samples.map((s) =>
    s.moonAlt > 0
      ? moonlightNl({ illumination: moonIllum, altitudeDeg: s.moonAlt, separationDeg: s.moonSep, targetAltitudeDeg: s.alt })
      : null,
  );
  const out: FilterNight[] = [];
  for (const letter of letters) {
    const fs = skies[letter];
    if (!fs) continue;
    let worth = 0;
    let sky = 0;
    samples.forEach((_, i) => {
      const sk = skyRate(fs, moon[i]);
      sky += sk;
      worth += sk > 0 ? fs.darkRate / sk : 0;
    });
    out.push({
      letter,
      hours: (samples.length * stepMinutes) / 60,
      efficiency: samples.length ? worth / samples.length : 0,
      meanSky: samples.length ? sky / samples.length : 0,
    });
  }
  return out;
}
