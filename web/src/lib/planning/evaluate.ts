/**
 * One target, one night, every filter: how much SNR² the night buys, and how
 * that compares with the same hours under a moonless sky.
 *
 * SNR² is what adds across nights (AstroPlanner's nightsToGoal), so it is what
 * is accumulated here; `efficiency` = SNR²(tonight) / SNR²(same hours, no moon)
 * is the "predicted per-filter SNR loss vs. a moonless night" of PLAN §6.2.
 */

import type { Camera } from "@/lib/snr";
import type { FilterLetter, PlanRig } from "./rig";
import type { FilterSignal } from "./signal";
import { atAltitude, moonlightNl, skyRate, type FilterSky } from "./skymodel";
import type { TargetSample } from "./night";

export type FilterNight = {
  letter: FilterLetter;
  hours: number;
  snr2: number;
  snr2Dark: number;
  /** 0–1: fraction of a moonless night's SNR² that tonight delivers. */
  efficiency: number;
  /** Mean sky rate over the usable samples, e⁻/s/px. */
  meanSky: number;
};

const variance = (obj: number, sky: number, cam: Camera, sub: number) => obj + sky + cam.darkCurrentEPerSec + (cam.readNoiseE * cam.readNoiseE) / sub;

export function evaluateNight(
  samples: TargetSample[],
  stepMinutes: number,
  moonIllum: number,
  signals: FilterSignal[],
  skies: Partial<Record<FilterLetter, FilterSky>>,
  rig: PlanRig,
): FilterNight[] {
  const dt = stepMinutes * 60;
  const moon = samples.map((s) =>
    s.moonAlt > 0
      ? moonlightNl({ illumination: moonIllum, altitudeDeg: s.moonAlt, separationDeg: s.moonSep, targetAltitudeDeg: s.alt })
      : null,
  );
  const out: FilterNight[] = [];
  for (const sig of signals) {
    const fs = skies[sig.letter];
    if (!fs) continue;
    const sub = rig.subSeconds[sig.letter] ?? 300;
    let snr2 = 0;
    let snr2Dark = 0;
    let sky = 0;
    samples.forEach((s, i) => {
      const obj = atAltitude(sig.zenithRate, fs.filter, s.alt);
      if (obj <= 0) return;
      const sk = skyRate(fs, moon[i]);
      sky += sk;
      snr2 += (dt * obj * obj) / variance(obj, sk, rig.camera, sub);
      snr2Dark += (dt * obj * obj) / variance(obj, fs.darkRate, rig.camera, sub);
    });
    out.push({
      letter: sig.letter,
      hours: (samples.length * stepMinutes) / 60,
      snr2,
      snr2Dark,
      // No modelled signal in this filter: worth nothing, whatever the sky.
      efficiency: snr2Dark > 0 ? snr2 / snr2Dark : 0,
      meanSky: samples.length ? sky / samples.length : 0,
    });
  }
  return out;
}

/** SNR² per hour under a reference sky (moonless, given altitude) — converts SNR goals to hours. */
export function snr2PerHour(sig: FilterSignal, fs: FilterSky, rig: PlanRig, altitudeDeg = 50): number {
  const obj = atAltitude(sig.zenithRate, fs.filter, altitudeDeg);
  if (obj <= 0) return 0;
  const sub = rig.subSeconds[sig.letter] ?? 300;
  return (3600 * obj * obj) / variance(obj, fs.darkRate, rig.camera, sub);
}
