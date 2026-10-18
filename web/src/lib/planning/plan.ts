/**
 * Planning, put together: for a site and a starting night, rank your own
 * targets and new ideas, suggest a filter per night, and turn goals into hours.
 *
 * Score = usable hours × predicted SNR efficiency × need × preference (PLAN §6.2):
 *  - usable hours: astronomical darkness, above the site floor and horizon mask;
 *  - efficiency: tonight's SNR² vs. the same hours with no moon, per filter;
 *  - need: the remaining goal deficit for that filter (0–1); without goals,
 *    filters that lag behind the target's best-covered one are wanted more;
 *  - moon sensitivity: on moonless hours, filters the moon hurts most (OIII,
 *    L, B) get a bonus, so dark time goes to them and Hα/SII are kept for
 *    moonlit nights — the opportunity cost a single night's efficiency misses;
 *  - preference: acquiring targets 1, processed 0.6, finished 0 (shown, not ranked).
 * New ideas are also weighted by brightness (hours to SNR 30) and framing fit.
 */

import { angularSeparation } from "@/lib/astro/angles";
import { OBJECT_CLASS_LABEL, type ObjectClass } from "@/lib/snr";
import type { SiteCalibration } from "./calibrate";
import { evaluateNight, snr2PerHour, type FilterNight } from "./evaluate";
import { grids, maxDarkAltitude, usableHours, usableSamples, type NightGrid, type TargetSample } from "./night";
import { FILTER_LETTERS, FILTER_NAMES, type FilterLetter, type PlanRig } from "./rig";
import { classFilters, halphaAnchor, targetSignals, type CatalogInfo, type FilterSignal, type HalphaAnchor } from "./signal";
import { filterSky, type FilterSky } from "./skymodel";
import { goalStatus, type GoalStatus } from "./goals";
import type { NightForecast } from "./forecast";
import { nightSky, ruleFilter, type RuleChoice } from "./skyrule";
import type { HalphaMeasurement, PlanTarget, Site } from "./types";

export { goalStatus, type GoalStatus };


export type TargetPlan = {
  key: string;
  kind: "library" | "idea";
  target: PlanTarget | null;
  info: CatalogInfo | null;
  title: string;
  subtitle: string;
  ra: number;
  dec: number;
  objectClass: ObjectClass;
  classLabel: string;
  anchor: HalphaAnchor;
  signals: FilterSignal[];
  goals: GoalStatus[];
  goalsSet: boolean;
  tonight: FilterNight[];
  tonightHours: number;
  maxAlt: number;
  best: { letter: FilterLetter; value: number; efficiency: number } | null;
  advice: string;
  score: number;
  hours30: number;
  hours90: number;
  /** Usable hours per night for the season strip (library targets only). */
  season?: number[];
  fit: number;
  /** Filters considered for this target, in suggestion order. */
  relevant: FilterLetter[];
  /** Per filter, 0–1: SNR² lost under a reference bright moon (75 % lit, 60° away). */
  moonSensitivity: Partial<Record<FilterLetter, number>>;
  /** Fraction of tonight's usable time with the moon down. */
  moonDownFraction: number;
  /** Hours to SNR 30 in the easiest relevant filter under a dark sky (ideas' brightness). */
  hoursToSnr30: number | null;
  /** Your sky rules' choice for tonight (skyrule.ts), when the forecast covers it; it sets `best`. */
  rule: RuleChoice | null;
  /** On one of your Telescopius target lists (ideas only). */
  listed?: boolean;
};

/**
 * Value of spending a night's usable time on one filter (see the header).
 * The opportunity term is symmetric: moon-sensitive filters gain on moonless
 * hours and lose on moonlit ones, so dark time is not spent on Hα and full-moon
 * time is not spent on luminance just because that filter lags behind.
 */
export function filterValue(f: FilterNight, need: number, sensitivity: number, moonDownFraction: number): number {
  return f.hours * f.efficiency * need * Math.max(0, 1 + (2 * moonDownFraction - 1) * sensitivity);
}

const REFERENCE_MOON: TargetSample = { t: 0, alt: 50, az: 180, moonAlt: 40, moonSep: 60 };

export type CalendarNight = {
  date: string;
  dusk: number | null;
  dawn: number | null;
  darkHours: number;
  moonIllum: number;
  moonPhase: number;
  moonFreeHours: number;
  best: { key: string; title: string; letter: FilterLetter; hours: number; efficiency: number } | null;
};

export type PlanResult = {
  site: Site;
  date: string;
  tonight: NightGrid;
  rig: PlanRig;
  skies: Partial<Record<FilterLetter, FilterSky>>;
  calibration: SiteCalibration | null;
  yours: TargetPlan[];
  ideas: TargetPlan[];
  calendar: CalendarNight[];
  computeMs: number;
};

const STAGE_WEIGHT: Record<string, number> = { acquiring: 1, processed: 0.6, finished: 0 };

export function siteSkies(rig: PlanRig, site: Site, cal: SiteCalibration | null): Partial<Record<FilterLetter, FilterSky>> {
  const out: Partial<Record<FilterLetter, FilterSky>> = {};
  for (const letter of FILTER_LETTERS) {
    const f = rig.filters[letter];
    if (f) out[letter] = filterSky(rig, f, site.sqm, cal?.filters[letter]?.kappa ?? 1);
  }
  return out;
}

const pct = (x: number) => `${Math.round(x * 100)} %`;

function adviceFor(plan: Pick<TargetPlan, "tonight" | "relevant" | "best" | "tonightHours" | "moonSensitivity" | "rule">, grid: NightGrid): string {
  if (!grid.samples.length) return "No astronomical darkness tonight.";
  if (plan.tonightHours <= 0) return "Not above the horizon mask during darkness tonight.";
  if (!plan.best) return "Nothing left to shoot: every goal is met.";
  if (plan.rule) return plan.rule.note;
  const rel = plan.tonight.filter((f) => plan.relevant.includes(f.letter));
  const worst = [...rel]
    .filter((f) => f.snr2Dark > 0 && f.efficiency < 0.8 && f.letter !== plan.best!.letter)
    .sort((a, b) => a.efficiency - b.efficiency);
  const moonUp = grid.moonIllum > 0.05 && plan.tonight.some((f) => f.efficiency < 0.97);
  const name = FILTER_NAMES[plan.best.letter];
  if (moonUp) {
    const tail = worst.length ? `; ${worst.slice(0, 2).map((f) => `${FILTER_NAMES[f.letter]} keeps ${pct(f.efficiency)}`).join(", ")} of a moonless night` : "";
    return `Moon ${pct(grid.moonIllum)} lit → shoot ${name}${tail}.`;
  }
  const sens = plan.moonSensitivity;
  const tolerant = plan.relevant.filter((l) => l !== plan.best!.letter && (sens[l] ?? 1) < 0.25 && (sens[plan.best!.letter] ?? 0) >= 0.25);
  return tolerant.length
    ? `Dark sky → shoot ${name}; keep ${tolerant.map((l) => FILTER_NAMES[l]).join("/")} for moonlit nights.`
    : `Dark sky → shoot ${name}.`;
}

export type PlanInput = {
  site: Site;
  date: string;
  rig: PlanRig;
  targets: PlanTarget[];
  catalogFor: (t: PlanTarget) => CatalogInfo | null;
  ideas: CatalogInfo[];
  halpha: Record<string, HalphaMeasurement | null>;
  calibration: SiteCalibration | null;
  goalCapHours: number;
  includeFinished?: boolean;
  calendarDays?: number;
  seasonDays?: number;
  /** Keep every idea (no "already covered" or "not up" filtering) and give it a season strip — for a detail page. */
  keepAllIdeas?: boolean;
  /** Lower-cased names of ideas on your Telescopius lists: kept whatever their rank or season. */
  listed?: Set<string>;
  /** Astrospheric's forecast cut to a night; nights it covers use your sky rules for the filter. */
  forecastFor?: (dusk: number | null, dawn: number | null) => NightForecast | null;
};

export const halphaKey = (ra: number, dec: number) => `${ra.toFixed(3)}|${dec.toFixed(3)}`;

export function plan(input: PlanInput): PlanResult {
  const t0 = Date.now();
  const { site, date, rig } = input;
  const skies = siteSkies(rig, site, input.calibration);
  const calendarDays = input.calendarDays ?? 30;
  const nights = grids(site, date, calendarDays, 10);
  const tonight = nights[0];
  const coarse90 = grids(site, date, 90, 20, false);
  const skyOf = (g: NightGrid) => nightSky(input.forecastFor?.(g.dusk, g.dawn) ?? null);
  const tonightSky = skyOf(tonight);

  const build = (kind: TargetPlan["kind"], target: PlanTarget | null, info: CatalogInfo | null, ra: number, dec: number): TargetPlan => {
    const cls: ObjectClass = info?.objectClass ?? "unknown";
    const anchor = halphaAnchor(cls, input.halpha[halphaKey(info?.ra ?? ra, info?.dec ?? dec)] ?? null);
    const signals = targetSignals(rig, info ?? { name: target?.catalogId ?? "?", ra, dec, majAxArcmin: null, minAxArcmin: null, paDeg: null, type: "", vmag: null, surfBr: null, commonName: null, objectClass: cls }, anchor);
    const samples = usableSamples(tonight, site, ra, dec);
    const tonightEval = evaluateNight(samples, tonight.stepMinutes, tonight.moonIllum, signals, skies, rig);
    const h90 = coarse90.map((g) => usableHours(g, site, ra, dec));
    const hours90 = h90.reduce((a, b) => a + b, 0);
    const hours30 = h90.slice(0, 30).reduce((a, b) => a + b, 0);

    // Goals: per filter the target has data in, has a goal for, or that suits its class.
    const goals: GoalStatus[] = [];
    const goalsSet = !!target && Object.keys(target.goals).length > 0;
    let relevant: FilterLetter[];
    if (target) {
      const have = new Set<string>([...target.filters.map((f) => f.filter), ...Object.keys(target.goals)]);
      const letters = FILTER_LETTERS.filter((l) => have.has(l));
      relevant = letters.length ? letters : classFilters(cls, rig.owned);
      for (const letter of relevant) {
        const sig = signals.find((s) => s.letter === letter);
        const fs = skies[letter];
        const rate = sig && fs ? snr2PerHour(sig, fs, rig) : 0;
        const tot = target.filters.find((f) => f.filter === letter);
        const st = goalStatus(letter, target.goals[letter] ?? null, tot?.goodSeconds ?? 0, tot?.seconds ?? 0, rate, input.goalCapHours, hours90);
        goals.push(st);
      }
      if (!goalsSet) {
        // No goals at all: balance the filters — the ones lagging behind are wanted more.
        const most = Math.max(...goals.map((g) => g.goodHours), 0);
        for (const g of goals) g.need = most > 0 ? 0.25 + 0.5 * (1 - g.goodHours / most) : 0.5;
      }
    } else {
      relevant = classFilters(cls, rig.owned);
    }
    const needOf = (l: FilterLetter) => (target ? goals.find((g) => g.letter === l)?.need ?? 0 : 1);
    const moonSensitivity: TargetPlan["moonSensitivity"] = {};
    for (const f of evaluateNight([REFERENCE_MOON], 60, 0.75, signals.filter((s) => relevant.includes(s.letter)), skies, rig)) {
      moonSensitivity[f.letter] = Math.max(0, 1 - f.efficiency);
    }
    const moonDownFraction = samples.length ? samples.filter((s) => s.moonAlt <= 0).length / samples.length : 1;
    let best: TargetPlan["best"] = null;
    for (const f of tonightEval) {
      if (!relevant.includes(f.letter) || f.hours <= 0) continue;
      const value = filterValue(f, needOf(f.letter), moonSensitivity[f.letter] ?? 0, moonDownFraction);
      if (value > 0 && (!best || value > best.value)) best = { letter: f.letter, value, efficiency: f.efficiency };
    }
    const rule = ruleFilter(tonightSky, tonightEval, relevant, needOf);
    if (rule) {
      const f = tonightEval.find((x) => x.letter === rule.letter)!;
      best = { letter: rule.letter, value: filterValue(f, needOf(rule.letter), moonSensitivity[rule.letter] ?? 0, moonDownFraction), efficiency: f.efficiency };
    }
    // Brightness: hours to SNR 30 in the easiest relevant filter, under this site's dark sky.
    let hoursToSnr30: number | null = null;
    for (const l of relevant) {
      const sig = signals.find((s) => s.letter === l);
      const fs = skies[l];
      const rate = sig && fs ? snr2PerHour(sig, fs, rig) : 0;
      if (rate > 0) hoursToSnr30 = Math.min(hoursToSnr30 ?? Infinity, 900 / rate);
    }
    const size = info?.majAxArcmin ?? null;
    const fovArcmin = rig.fov.width * 60;
    // Framing: best when the object spans ~60 % of the field width; log-normal fall-off, floor 0.2.
    const fit = size == null ? 1 : Math.max(0.2, Math.exp(-(Math.log(size / (fovArcmin * 0.6)) ** 2) / 2));
    const brightness = hoursToSnr30 == null ? 0.05 : Math.min(1, Math.max(0.05, 8 / hoursToSnr30));
    const weight = kind === "library" ? STAGE_WEIGHT[target?.stage ?? "acquiring"] ?? 1 : 0.8 * fit * brightness;
    const p: TargetPlan = {
      key: kind === "library" ? target!.id : `idea:${info!.name}`,
      kind,
      target,
      info,
      title: target?.catalogId ?? info!.name,
      subtitle: target?.name ?? info?.commonName ?? "",
      ra,
      dec,
      objectClass: cls,
      classLabel: OBJECT_CLASS_LABEL[cls],
      anchor,
      signals,
      goals,
      goalsSet,
      tonight: tonightEval,
      tonightHours: (samples.length * tonight.stepMinutes) / 60,
      maxAlt: maxDarkAltitude(tonight, ra, dec),
      best,
      advice: "",
      score: (best?.value ?? 0) * weight,
      hours30,
      hours90,
      fit,
      relevant,
      moonSensitivity,
      moonDownFraction,
      hoursToSnr30,
      rule,
    };
    p.advice = adviceFor(p, tonight);
    return p;
  };

  const yours: TargetPlan[] = [];
  for (const t of input.targets) {
    const info = input.catalogFor(t);
    const ra = t.pointing?.ra ?? info?.ra;
    const dec = t.pointing?.dec ?? info?.dec;
    if (ra == null || dec == null) continue;
    if (t.stage === "finished" && !input.includeFinished) continue;
    yours.push(build("library", t, info, ra, dec));
  }
  yours.sort((a, b) => b.score - a.score || b.tonightHours - a.tonightHours);

  // Season strips for your targets: a year of usable hours, coarse.
  const seasonDays = input.seasonDays ?? 365;
  if (seasonDays > 0) {
    const year = grids(site, date, seasonDays, 20, false);
    for (const p of yours) p.season = year.map((g) => usableHours(g, site, p.ra, p.dec));
  }

  const owned = new Set(input.targets.map((t) => t.catalogId.toLowerCase()));
  const pointings = input.targets.flatMap((t) => (t.pointing ? [t.pointing] : []));
  // Already in your library, or inside one of your fields: not a new idea.
  const covered = (info: CatalogInfo) =>
    owned.has(info.name.toLowerCase()) ||
    pointings.some((p) => angularSeparation(p.ra, p.dec, info.ra, info.dec) < Math.max(0.5, rig.fov.height / 2));
  const ideaPlans: TargetPlan[] = [];
  for (const info of input.ideas) {
    // Targets you listed yourself are kept even out of season (they show 0 h), unless already in your library.
    const listed = input.listed?.has(info.name.toLowerCase()) ?? false;
    if (!input.keepAllIdeas) {
      if (covered(info)) continue;
      if (!listed && maxDarkAltitude(tonight, info.ra, info.dec) < site.minAlt + 5 && usableHours(coarse90[45] ?? tonight, site, info.ra, info.dec) <= 0) continue;
    }
    const p = build("idea", null, info, info.ra, info.dec);
    p.listed = listed;
    if (p.hours90 <= 0 && !input.keepAllIdeas && !listed) continue;
    if (input.keepAllIdeas && seasonDays > 0) p.season = grids(site, date, seasonDays, 20, false).map((g) => usableHours(g, site, p.ra, p.dec));
    ideaPlans.push(p);
  }
  // The top new ideas — by score, and by tonight's and the next 90 nights' hours so they can be re-sorted
  // by either — plus every listed target wherever it ranks. Kept in score order.
  const unlisted = ideaPlans.filter((p) => !p.listed);
  const top = (by: (a: TargetPlan, b: TargetPlan) => number) => [...unlisted].sort(by).slice(0, 60);
  const shownIdeas = [
    ...new Set([
      ...top((a, b) => b.score - a.score || b.hours90 - a.hours90),
      ...top((a, b) => b.tonightHours - a.tonightHours || b.score - a.score),
      ...top((a, b) => b.hours90 - a.hours90 || b.score - a.score),
      ...ideaPlans.filter((p) => p.listed),
    ]),
  ].sort((a, b) => b.score - a.score || b.hours90 - a.hours90);

  // Calendar: per night, the best of your targets and its filter.
  const calendar: CalendarNight[] = nights.map((g, i) => {
    let best: CalendarNight["best"] = null;
    if (i === 0) {
      const top = yours.find((p) => p.best);
      if (top?.best) best = { key: top.key, title: top.title, letter: top.best.letter, hours: top.tonightHours, efficiency: top.best.efficiency };
    } else {
      let bestValue = 0;
      const sky = skyOf(g);
      for (const p of yours) {
        const weight = STAGE_WEIGHT[p.target?.stage ?? "acquiring"] ?? 1;
        if (weight === 0) continue;
        const samples = usableSamples(g, site, p.ra, p.dec);
        if (!samples.length) continue;
        const evals = evaluateNight(samples, g.stepMinutes, g.moonIllum, p.signals.filter((s) => p.relevant.includes(s.letter)), skies, rig);
        const down = samples.filter((s) => s.moonAlt <= 0).length / samples.length;
        const needOf = (l: FilterLetter) => p.goals.find((x) => x.letter === l)?.need ?? 0.5;
        const rule = ruleFilter(sky, evals, p.relevant, needOf);
        for (const f of evals) {
          if (rule && f.letter !== rule.letter) continue;
          const need = needOf(f.letter);
          const value = filterValue(f, need, p.moonSensitivity[f.letter] ?? 0, down) * weight;
          if (value > bestValue) {
            bestValue = value;
            best = { key: p.key, title: p.title, letter: f.letter, hours: f.hours, efficiency: f.efficiency };
          }
        }
      }
    }
    return { date: g.date, dusk: g.dusk, dawn: g.dawn, darkHours: g.darkHours, moonIllum: g.moonIllum, moonPhase: g.moonPhase, moonFreeHours: g.moonFreeHours, best };
  });

  return {
    site,
    date,
    tonight,
    rig,
    skies,
    calibration: input.calibration,
    yours,
    ideas: shownIdeas,
    calendar,
    computeMs: Date.now() - t0,
  };
}
