// VENDORED from D:\Python\AstroPlanner\src\lib\ephemeris.ts (2026-10-09).
// Only change: import paths (angularSeparation from ./angles, since AstroPlanner's wcs.ts is not vendored; sky from ../snr).
/**
 * When is the target actually up, and how dark is it while it is?
 *
 * This is what turns an SNR curve into a plan. "OIII needs 11.4 hours" is not
 * actionable until you know your target clears 30 degrees for 4.2 hours of
 * astronomical darkness tonight, and that the moon is up for half of it.
 *
 * Wraps astronomy-engine (MIT, no dependencies). Note its RA convention: right
 * ascension is in SIDEREAL HOURS, not degrees, so everything crossing that
 * boundary is divided by 15.
 */

import {
  Body,
  Equator,
  Horizon,
  Illumination,
  Observer,
  SearchAltitude,
  SearchRiseSet,
} from "astronomy-engine";

import { angularSeparation } from "./angles";
import { effectiveSkyMag, type MoonGeometry } from "../snr/sky";

export type Site = {
  latitude: number;
  longitude: number;
  elevationM: number;
};

export type SkyPos = { ra: number; dec: number };

const observerFor = (site: Site) =>
  new Observer(site.latitude, site.longitude, site.elevationM);

/** Altitude and azimuth of a fixed target, in degrees. */
export function altitudeOf(site: Site, target: SkyPos, when: Date) {
  const horizon = Horizon(
    when,
    observerFor(site),
    target.ra / 15, // degrees -> sidereal hours
    target.dec,
    "normal", // include atmospheric refraction
  );
  return { altitude: horizon.altitude, azimuth: horizon.azimuth };
}

export type NightWindow = {
  /** Sun reaches -18 degrees; astronomical darkness begins. */
  duskUtc: Date;
  dawnUtc: Date;
  darkHours: number;
  sunsetUtc: Date | null;
  sunriseUtc: Date | null;
};

/**
 * Astronomical darkness for the night that begins on `date` at this site.
 *
 * Returns null when the sun never reaches -18 degrees — which is not an error
 * but a fact of northern summers, and the caller must say so rather than
 * quietly reporting zero hours.
 */
export function nightWindow(site: Site, date: Date): NightWindow | null {
  const observer = observerFor(site);

  // Start from local noon so "the night of the 4th" means the one beginning
  // that evening, not the one that ended that morning.
  const noonLocal = new Date(date);
  noonLocal.setHours(12, 0, 0, 0);

  const dusk = SearchAltitude(Body.Sun, observer, -1, noonLocal, 1, -18);
  if (!dusk) return null;

  const dawn = SearchAltitude(Body.Sun, observer, +1, dusk.date, 1, -18);
  if (!dawn) return null;

  const sunset = SearchRiseSet(Body.Sun, observer, -1, noonLocal, 1);
  const sunrise = SearchRiseSet(Body.Sun, observer, +1, dusk.date, 1);

  const darkHours = (dawn.date.getTime() - dusk.date.getTime()) / 3_600_000;

  return {
    duskUtc: dusk.date,
    dawnUtc: dawn.date,
    darkHours,
    sunsetUtc: sunset?.date ?? null,
    sunriseUtc: sunrise?.date ?? null,
  };
}

/** Moon altitude, illuminated fraction, and separation from the target. */
export function moonState(site: Site, target: SkyPos, when: Date): MoonGeometry {
  const observer = observerFor(site);

  const moonEq = Equator(Body.Moon, when, observer, true, true);
  const moonHorizon = Horizon(when, observer, moonEq.ra, moonEq.dec, "normal");
  const targetHorizon = Horizon(when, observer, target.ra / 15, target.dec, "normal");

  return {
    illumination: Illumination(Body.Moon, when).phase_fraction,
    altitudeDeg: moonHorizon.altitude,
    // moonEq.ra is in hours; angularSeparation wants degrees.
    separationDeg: angularSeparation(target.ra, target.dec, moonEq.ra * 15, moonEq.dec),
    targetAltitudeDeg: targetHorizon.altitude,
  };
}

export type TrackSample = {
  time: Date;
  altitude: number;
  azimuth: number;
  moonAltitude: number;
  /** Sky brightness including moonlight, mag/arcsec^2. */
  skyMag: number;
  isDark: boolean;
  isUsable: boolean;
};

export type TrackOptions = {
  /** Minimum target altitude to count as usable. */
  minAltitudeDeg: number;
  /** Site sky brightness with no moon. */
  siteSqm: number;
  stepMinutes?: number;
};

/**
 * Samples the whole night: where the target is, where the moon is, and how
 * bright the sky is at each step.
 */
export function nightTrack(
  site: Site,
  target: SkyPos,
  night: NightWindow,
  options: TrackOptions,
): TrackSample[] {
  const step = (options.stepMinutes ?? 10) * 60_000;

  // Extend an hour either side of darkness so the chart shows the target
  // climbing into and out of the usable window rather than starting mid-air.
  const from = night.duskUtc.getTime() - 3_600_000;
  const to = night.dawnUtc.getTime() + 3_600_000;

  const out: TrackSample[] = [];
  for (let t = from; t <= to; t += step) {
    const when = new Date(t);
    const { altitude, azimuth } = altitudeOf(site, target, when);
    const moon = moonState(site, target, when);
    const isDark = t >= night.duskUtc.getTime() && t <= night.dawnUtc.getTime();

    out.push({
      time: when,
      altitude,
      azimuth,
      moonAltitude: moon.altitudeDeg,
      skyMag: effectiveSkyMag(options.siteSqm, moon),
      isDark,
      isUsable: isDark && altitude >= options.minAltitudeDeg,
    });
  }
  return out;
}

export type NightSummary = {
  date: Date;
  /** Hours of astronomical darkness with the target above the altitude floor. */
  usableHours: number;
  /** Mean sky brightness across those hours, moonlight included. */
  meanSkyMag: number;
  /**
   * Mean target altitude across the usable hours — the altitude that should
   * drive extinction, rather than the peak the target only briefly reaches.
   */
  meanUsableAltitude: number;
  maxAltitude: number;
  moonIllumination: number;
  /** True when the sun never reaches -18 degrees. */
  noAstronomicalDarkness: boolean;
};

export function summariseNight(
  site: Site,
  target: SkyPos,
  date: Date,
  options: TrackOptions,
): NightSummary {
  const night = nightWindow(site, date);

  if (!night) {
    return {
      date,
      usableHours: 0,
      meanSkyMag: options.siteSqm,
      meanUsableAltitude: 0,
      maxAltitude: 0,
      moonIllumination: Illumination(Body.Moon, date).phase_fraction,
      noAstronomicalDarkness: true,
    };
  }

  const stepMinutes = options.stepMinutes ?? 10;
  const samples = nightTrack(site, target, night, options);
  const usable = samples.filter((s) => s.isUsable);

  // Sky brightness averaged in LINEAR units, not magnitudes: an hour of
  // moonlight and an hour of dark do not average to the magnitude midpoint.
  const meanLinear =
    usable.length > 0
      ? usable.reduce((acc, s) => acc + Math.pow(10, -0.4 * s.skyMag), 0) / usable.length
      : Math.pow(10, -0.4 * options.siteSqm);

  return {
    date,
    usableHours: (usable.length * stepMinutes) / 60,
    meanSkyMag: -2.5 * Math.log10(meanLinear),
    meanUsableAltitude:
      usable.length > 0
        ? usable.reduce((acc, s) => acc + s.altitude, 0) / usable.length
        : 0,
    maxAltitude: samples.reduce((m, s) => Math.max(m, s.altitude), -90),
    moonIllumination: Illumination(Body.Moon, night.duskUtc).phase_fraction,
    noAstronomicalDarkness: false,
  };
}

export type NightsToGoalResult = {
  /** Nights actually used, in order. */
  nights: (NightSummary & { snrAfter: number })[];
  /** Total usable hours consumed. */
  totalHours: number;
  /** True when the goal was reached inside the search window. */
  reached: boolean;
  /** SNR attained by the end of the window. */
  finalSnr: number;
};

/**
 * How many real nights it takes to reach a target SNR.
 *
 * SNR does not add across nights, but SNR SQUARED does — each night contributes
 * `hours * objectRate^2 / variance` to the running total, and the variance is
 * different on a moonlit night than on a dark one. Summing hours and applying
 * one average sky at the end would flatter a moonlit run.
 *
 * `varianceFor(skyMag)` lets the caller supply the filter's own noise model,
 * so this stays free of any particular rig.
 */
export function nightsToGoal(
  site: Site,
  target: SkyPos,
  startDate: Date,
  searchDays: number,
  goal: {
    targetSnr: number;
    objectRate: number;
    varianceFor: (skyMag: number) => number;
  },
  options: TrackOptions,
): NightsToGoalResult {
  const nights: (NightSummary & { snrAfter: number })[] = [];
  let snrSquared = 0;
  let totalHours = 0;

  for (let i = 0; i < searchDays; i++) {
    const date = new Date(startDate);
    date.setDate(date.getDate() + i);

    const summary = summariseNight(site, target, date, options);
    if (summary.usableHours <= 0) continue;

    const seconds = summary.usableHours * 3600;
    snrSquared +=
      (seconds * goal.objectRate * goal.objectRate) / goal.varianceFor(summary.meanSkyMag);
    totalHours += summary.usableHours;

    const snrAfter = Math.sqrt(snrSquared);
    nights.push({ ...summary, snrAfter });

    if (snrAfter >= goal.targetSnr) {
      return { nights, totalHours, reached: true, finalSnr: snrAfter };
    }
  }

  return {
    nights,
    totalHours,
    reached: false,
    finalSnr: Math.sqrt(snrSquared),
  };
}
