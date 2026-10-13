/**
 * Your filter rules, applied on nights Astrospheric forecasts:
 *  - OIII sky: clear, good transparency and little moonlight on the target →
 *    OIII (L/RGB for broadband targets). OIII is the most moon- and
 *    haze-sensitive line, and dark transparent nights are the scarce ones.
 *  - Hα sky: otherwise, unless the seeing is poor → Hα, which often becomes the
 *    luminance, so it gets the steadiest air the night can offer.
 *  - SII sky: everything else → SII (colour only, moon-tolerant).
 * Seeing is a veto rather than a trigger: at ~2.4″/px only poor seeing visibly
 * blurs, and Astrospheric's seeing is its coarsest forecast.
 * A filter whose goal is met is skipped for the next one in the order.
 */

import { CLEAR_CLOUD, type NightForecast } from "./forecast";
import type { FilterNight } from "./evaluate";
import { FILTER_NAMES, type FilterLetter } from "./rig";

export type SkyClass = "oiii" | "ha" | "sii";

/** The clear part of a night, as the rules see it (means over clear dark hours). */
export type NightSky = { transparency: number | null; seeing: number | null };

/** Astrospheric transparency at or below this is "good" (its scale runs ~10 excellent … 29 poor). */
export const GOOD_TRANSPARENCY = 15;
/** Astrospheric seeing (1–5) below this is "poor". */
export const POOR_SEEING = 2;
/** OIII (or L) keeps at least this share of a moonless night's SNR² on the target: "little moonlight". */
export const LITTLE_MOONLIGHT = 0.8;

const ORDER: Record<SkyClass, FilterLetter[]> = {
  oiii: ["O", "L", "B", "G", "R"],
  ha: ["H", "S"],
  sii: ["S", "H"],
};

const LABEL: Record<SkyClass, string> = { oiii: "OIII sky", ha: "Hα sky", sii: "SII sky" };

/** Null when the night has no clear dark hour to judge (the moon-only choice stays). */
export function nightSky(f: NightForecast | null): NightSky | null {
  if (!f) return null;
  const clear = f.hours.filter((h) => h.cloud && h.cloud.value < CLEAR_CLOUD);
  if (!clear.length) return null;
  const sky = { transparency: mean(clear.map((h) => h.transparency?.value)), seeing: mean(clear.map((h) => h.seeing?.value)) };
  // The extended forecast is cloud only: with neither value the rules cannot judge (moon-only choice).
  return sky.transparency == null && sky.seeing == null ? null : sky;
}

export type RuleChoice = { letter: FilterLetter; sky: SkyClass; note: string };

/**
 * The filter your rules pick for one target on one night, or null when they do
 * not apply (no forecast, nothing clear, or none of the rule's filters is wanted).
 */
export function ruleFilter(
  sky: NightSky | null,
  evals: FilterNight[],
  relevant: FilterLetter[],
  needOf: (l: FilterLetter) => number,
): RuleChoice | null {
  if (!sky) return null;
  const eff = (l: FilterLetter) => evals.find((f) => f.letter === l)?.efficiency;
  const moonlight = eff("O") ?? eff("L") ?? eff("B");
  const transparent = sky.transparency != null && sky.transparency <= GOOD_TRANSPARENCY;
  const steady = sky.seeing == null || sky.seeing >= POOR_SEEING;
  const cls: SkyClass = transparent && (moonlight ?? 0) >= LITTLE_MOONLIGHT ? "oiii" : steady ? "ha" : "sii";

  const usable = (l: FilterLetter) => relevant.includes(l) && (evals.find((f) => f.letter === l)?.snr2Dark ?? 0) > 0;
  const wanted = ORDER[cls].filter(usable);
  const pick = wanted.find((l) => needOf(l) > 0.05);
  if (!pick) return null;

  const why = [
    sky.transparency != null ? `transparency ${Math.round(sky.transparency)}` : null,
    cls === "oiii" ? "little moonlight" : moonlight != null && moonlight < LITTLE_MOONLIGHT ? "moonlit" : null,
    sky.seeing != null ? `seeing ${sky.seeing.toFixed(1)}/5` : null,
  ].filter(Boolean);
  const skipped = wanted.slice(0, wanted.indexOf(pick)).map((l) => FILTER_NAMES[l]);
  const note = `${LABEL[cls]} (${why.join(", ")}) → shoot ${FILTER_NAMES[pick]}${skipped.length ? `; ${skipped.join(", ")} goal met` : ""}.`;
  return { letter: pick, sky: cls, note };
}

function mean(xs: (number | undefined)[]): number | null {
  const v = xs.filter((x): x is number => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}
