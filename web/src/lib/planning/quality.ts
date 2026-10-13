/**
 * One verdict per night, for the Planning overview: is it worth setting up?
 *
 * Inputs are the night's darkness and moon (always known) and Astrospheric's
 * cloud forecast (hourly for ~3½ days, then the 3-hourly extended cloud
 * forecast to ~10 days). A night is worth it only with at least
 * MIN_RUN_HOURS of *consecutive* clear darkness: anything shorter, or clear
 * only in pieces, is "poor" (red). The moon then decides: "excellent" when it
 * is mostly down, "moonlit" (orange) when it is bright — it costs broadband
 * most but narrowband too, OIII especially. Beyond the forecast the verdict
 * rests on the darkness and the moon alone, and says so.
 *
 * GFS and ICON are second and third opinions over the hourly range: when they
 * often disagree with Astrospheric's clear/cloudy call, a clear night drops a
 * step (below AGREE_OK) or turns red (below AGREE_MIN). Smoke (RAP) names the
 * cause when transparency is poor: "smoky" instead of "hazy".
 */

import type { NightForecast } from "./forecast";
import type { CalendarNight } from "./plan";

export type QualityLevel = "excellent" | "good" | "moonlit" | "uncertain" | "poor" | "unknown";

export type NightQuality = {
  level: QualityLevel;
  label: string;
  /** Longest consecutive clear dark hours (forecast), or dark hours when the weather is not known yet. */
  hours: number;
  weatherKnown: boolean;
  /** The moon leaves most of the darkness dark (down, or a thin crescent). */
  moonDark: boolean;
  /** One short line under the verdict. */
  summary: string;
  /** Extra lines for a tooltip. */
  details: string[];
  /** 0–1: GFS and ICON agreeing with Astrospheric's clear/cloudy call (null beyond their range). */
  agreement: number | null;
  /** When the temperature first comes within 2° of the dew point (ms); null without dew risk or forecast. */
  dewFrom: number | null;
};

/** Consecutive clear dark hours a night needs to be worth setting up. */
export const MIN_RUN_HOURS = 5;
/** Model agreement below this costs a clear night one step (Excellent → Good, Good → Uncertain). */
export const AGREE_OK = 0.7;
/** Below this the models disagree too much to trust a clear forecast at all (red). */
export const AGREE_MIN = 0.4;
/** Column smoke above this counts as wildfire smoke (unit undocumented; 0 = none forecast). */
export const SMOKE_PRESENT = 0;

/** A night worth setting up for without compromise (a moonlit night is not counted). */
export const isGood = (q: NightQuality) => q.level === "excellent" || q.level === "good";

const h = (x: number) => `${x.toFixed(1)} h`;

export function nightQuality(n: CalendarNight, f: NightForecast | null): NightQuality {
  const moonShare = n.darkHours ? n.moonFreeHours / n.darkHours : 0;
  const moonDark = n.moonIllum < 0.25 || moonShare >= 0.6;
  const moonLine =
    n.moonIllum < 0.05 ? "New moon" : moonShare >= 0.95 ? `Moon ${Math.round(n.moonIllum * 100)} %, down all night` : `Moon ${Math.round(n.moonIllum * 100)} %, down ${h(n.moonFreeHours)} of ${h(n.darkHours)}`;
  const details = [`${h(n.darkHours)} of astronomical darkness`, moonLine];
  const agreement = f?.agreement ?? null;
  const dewFrom = f?.dewFrom ?? null;
  const poor = (label: string, summary: string, weatherKnown: boolean, hours: number): NightQuality =>
    ({ level: "poor", label, hours, weatherKnown, moonDark, summary, details, agreement, dewFrom });

  if (n.darkHours < MIN_RUN_HOURS) {
    return poor(n.darkHours <= 0 ? "No darkness" : "Too short", `Only ${h(n.darkHours)} of darkness`, false, n.darkHours);
  }

  if (!f || f.meanCloud == null) {
    return {
      level: "unknown",
      label: moonDark ? "Dark sky" : "Moonlit",
      hours: n.darkHours,
      weatherKnown: false,
      moonDark,
      summary: `${h(n.darkHours)} dark · no forecast yet`,
      details: [...details, "Beyond Astrospheric's forecast (about 10 days)"],
      agreement: null,
      dewFrom: null,
    };
  }

  const { seeing, transparency } = f;
  details.push(`Longest clear stretch ${h(f.longestClear)} (${h(f.clearHours)} clear in all)`);
  details.push(`Cloud ${Math.round(f.meanCloud)} % on average${f.covered < 0.95 ? ` (forecast covers ${Math.round(f.covered * 100)} % of the night)` : ""}`);
  if (seeing != null) details.push(`Seeing ${seeing.toFixed(1)} / 5`);
  if (transparency != null) details.push(`Transparency ${Math.round(transparency)} (10 excellent … 29 poor)`);
  if (f.dewRisk) details.push("Dew risk: temperature near the dew point");
  const smoky = f.smoke != null && f.smoke > SMOKE_PRESENT;
  if (smoky) details.push(`Wildfire smoke in the forecast (column smoke ${f.smoke!.toFixed(1)})`);
  if (f.extended) details.push("Extended forecast: 3-hourly cloud only, less reliable this far ahead");
  const modelLine = (name: string, m: { longestClear: number } | null) => (m ? `${name} ${h(m.longestClear)}` : null);
  if (agreement != null) {
    const runs = [modelLine("GFS", f.models.gfs), modelLine("ICON", f.models.icon)].filter(Boolean).join(", ");
    details.push(`Models agree ${Math.round(agreement * 100)} %${runs ? ` (longest clear: ${runs})` : ""}`);
  } else if (f.extended) details.push("Model agreement: GFS and ICON do not reach this night");

  if (f.longestClear >= MIN_RUN_HOURS) {
    const summary = `${h(f.longestClear)} clear in a row${moonDark ? "" : " · bright moon"}`;
    if (!moonDark) details.push("Bright moon: best for Hα and SII; OIII and broadband lose the most");
    let [level, label]: [QualityLevel, string] = !moonDark
      ? ["moonlit", "Clear · moonlit"]
      : transparency != null && transparency > 20 ? ["good", smoky ? "Good · smoky" : "Good · hazy"] : ["excellent", "Excellent"];
    // The other models disagree: trust the clear forecast less.
    if (agreement != null && agreement < AGREE_MIN) return poor("Models disagree", `${h(f.longestClear)} clear per Astrospheric only`, true, f.longestClear);
    if (agreement != null && agreement < AGREE_OK) {
      [level, label] = level === "excellent" ? ["good", "Good · uncertain"] : ["uncertain", level === "moonlit" ? "Uncertain · moonlit" : "Uncertain"];
    }
    return { level, label, hours: f.longestClear, weatherKnown: true, moonDark, summary, details, agreement, dewFrom };
  }
  // The forecast stops partway through the night: a clear run reaching its end may go on.
  if (f.uncoveredHours > 0 && f.clearAtEnd + f.uncoveredHours >= MIN_RUN_HOURS) {
    return {
      level: "unknown",
      label: "Possible",
      hours: f.clearAtEnd,
      weatherKnown: false,
      moonDark,
      summary: f.clearAtEnd > 0 ? `${h(f.clearAtEnd)} clear when the forecast ends` : "Late night not forecast yet",
      details: [...details, `The forecast ends ${h(f.uncoveredHours)} before dawn`],
      agreement,
      dewFrom,
    };
  }
  if (f.clearHours < 1) return poor("Cloudy", `${Math.round(f.meanCloud)} % cloud`, true, f.longestClear);
  if (f.clearHours >= MIN_RUN_HOURS) return poor("Intermittent", `Clear in pieces · longest ${h(f.longestClear)}`, true, f.longestClear);
  return poor("Too short", `Longest clear stretch ${h(f.longestClear)}`, true, f.longestClear);
}

/** First good night after the first one: forecast-backed if any, else the first dark-sky window beyond the forecast. */
export function nextGoodNight<T extends { night: CalendarNight; quality: NightQuality }>(nights: T[]) {
  const later = nights.slice(1);
  const forecast = later.find((x) => x.quality.weatherKnown && isGood(x.quality));
  if (forecast) return { ...forecast, basis: "forecast" as const };
  const dark = later.find((x) => x.quality.level === "unknown" && x.quality.moonDark);
  return dark ? { ...dark, basis: "moon" as const } : null;
}
