import "server-only";

import { engineGet, engineSend, type EngineResult } from "@/lib/engine";
import { calibrateSites, type SiteCalibration } from "@/lib/planning/calibrate";
import { nightForecast, type Forecast, type ForecastPayload, type NightForecast } from "@/lib/planning/forecast";
import { halphaKey, plan, type CalendarNight, type PlanResult } from "@/lib/planning/plan";
import { nightQuality, type NightQuality } from "@/lib/planning/quality";
import { buildRig, type PlanRig } from "@/lib/planning/rig";
import type { CatalogInfo } from "@/lib/planning/signal";
import { defaultSite } from "@/lib/planning/sites";
import type { HalphaMeasurement, PlanTarget, PlanningContext, SkyPayload } from "@/lib/planning/types";
import { findCatalog, ideaCandidates } from "./catalog";
import type { GoalRow } from "@/components/planning/GoalsEditor";
import { snr2PerHour } from "@/lib/planning/evaluate";
import { FILTER_NAMES } from "@/lib/planning/rig";

/**
 * Everything Planning needs from the engine, assembled on the server:
 * the planning context (targets, sites, settings, detected rig), the measured
 * sky (→ per-site calibration), and cached Hα survey values. Survey values not
 * measured yet are queued in the engine and appear on a later load.
 */

export type PlanningData = {
  context: PlanningContext;
  rig: PlanRig;
  calibration: Record<string, SiteCalibration>;
  halpha: Record<string, HalphaMeasurement | null>;
  halphaPending: number;
  catalogFor: (t: PlanTarget) => CatalogInfo | null;
};

/** Survey values only help objects the ~1° beam can resolve; library targets are always looked up (for context). */
const SURVEY_MIN_ARCMIN = 60;

export async function loadPlanningData(): Promise<EngineResult<PlanningData>> {
  const [ctx, sky] = await Promise.all([
    engineGet<PlanningContext>("/api/planning/context"),
    engineGet<SkyPayload>("/api/planning/sky"),
  ]);
  if (!ctx.ok) return ctx;
  const context = ctx.data;
  const rig = buildRig(context.detectedRig, context.settings.rig);
  const calibration = sky.ok ? calibrateSites(sky.data.samples, context.sites, rig) : {};

  const catalogFor = (t: PlanTarget) => findCatalog(t.catalogId, t.name);
  const items = new Map<string, { ra: number; dec: number; maj: number | null; min: number | null; pa: number | null }>();
  const add = (i: CatalogInfo) => items.set(halphaKey(i.ra, i.dec), { ra: i.ra, dec: i.dec, maj: i.majAxArcmin, min: i.minAxArcmin, pa: i.paDeg });
  for (const t of context.targets) {
    const info = catalogFor(t);
    if (info) add(info);
  }
  for (const i of ideaCandidates()) if ((i.majAxArcmin ?? 0) >= SURVEY_MIN_ARCMIN) add(i);
  const lookup = await engineSend<{ found: Record<string, HalphaMeasurement>; missing: number; queued: number }>(
    "/api/planning/halpha/lookup",
    { items: [...items.values()], prefetch: true },
  );
  return {
    ok: true,
    data: {
      context,
      rig,
      calibration,
      halpha: lookup.ok ? lookup.data.found : {},
      halphaPending: lookup.ok ? lookup.data.missing : 0,
      catalogFor,
    },
  };
}

/** The site a page asked for, else the default one. */
export const siteFor = (data: PlanningData, siteId: string | null | undefined) =>
  data.context.sites.find((s) => s.id === siteId) ?? defaultSite(data.context.sites) ?? null;

/** Cuts a forecast to one night, for plan(): nights it covers use the sky rules (skyrule.ts). */
const forecastCutter = (f: Forecast | null | undefined) => (f ? (dusk: number | null, dawn: number | null) => nightForecast(f, dusk, dawn) : undefined);

export function planFor(
  data: PlanningData,
  opts: { siteId?: string | null; date: string; includeFinished?: boolean; seasonDays?: number; calendarDays?: number; forecast?: Forecast | null },
): PlanResult | null {
  const site = siteFor(data, opts.siteId);
  if (!site) return null;
  return plan({
    site,
    date: opts.date,
    rig: data.rig,
    targets: data.context.targets,
    catalogFor: data.catalogFor,
    ideas: ideaCandidates(),
    halpha: data.halpha,
    calibration: data.calibration[site.id] ?? null,
    goalCapHours: data.context.settings.goalCapHours,
    includeFinished: opts.includeFinished,
    seasonDays: opts.seasonDays,
    calendarDays: opts.calendarDays,
    forecastFor: forecastCutter(opts.forecast),
  });
}

/** One target (library revision id, or "idea:<catalog name>") planned at one site; null when unknown. */
export function planOne(data: PlanningData, key: string, siteId: string | null | undefined, date: string, seasonDays = 365, forecast?: Forecast | null) {
  const site = siteFor(data, siteId);
  if (!site) return null;
  const target = data.context.targets.find((t) => t.id === key) ?? null;
  const idea = !target && key.startsWith("idea:") ? findCatalog(key.slice(5)) : null;
  if (!target && !idea) return null;
  const result = plan({
    site,
    date,
    rig: data.rig,
    targets: target ? [target] : [],
    catalogFor: data.catalogFor,
    ideas: idea ? [idea] : [],
    halpha: data.halpha,
    calibration: data.calibration[site.id] ?? null,
    goalCapHours: data.context.settings.goalCapHours,
    includeFinished: true,
    calendarDays: 1,
    seasonDays,
    keepAllIdeas: true,
    forecastFor: forecastCutter(forecast),
  });
  const p = result.yours[0] ?? result.ideas[0] ?? null;
  return p ? { site, result, plan: p, catalog: target ? data.catalogFor(target) : idea } : null;
}

/** Goal editor rows for a planned library target (both the Planning detail page and the Library target page). */
export function goalRowsFor(one: NonNullable<ReturnType<typeof planOne>>): GoalRow[] {
  const { plan: p, result } = one;
  if (!p.target) return [];
  return p.relevant.map((l) => {
    const sig = p.signals.find((s) => s.letter === l);
    const fs = result.skies[l];
    const tot = p.target!.filters.find((f) => f.filter === l);
    return {
      letter: l,
      name: FILTER_NAMES[l],
      goal: p.target!.goals[l] ?? null,
      goodSeconds: tot?.goodSeconds ?? 0,
      rawSeconds: tot?.seconds ?? 0,
      rate: sig && fs ? snr2PerHour(sig, fs, result.rig) : 0,
      basis: sig?.basis ?? "",
    };
  });
}

/** Astrospheric's forecast for a site, via the engine (which holds the key and caches it); null when the engine is down. */
export async function loadForecast(siteId: string): Promise<ForecastPayload | null> {
  const res = await engineGet<ForecastPayload>(`/api/planning/forecast?siteId=${encodeURIComponent(siteId)}`);
  return res.ok ? res.data : null;
}

export type RatedNight = { night: CalendarNight; quality: NightQuality; forecast: NightForecast | null };

/** Plan from `date` for `days` nights at a site, each night rated with the forecast (see quality.ts). */
export async function ratedNights(data: PlanningData, siteId: string | null | undefined, date: string, days: number, includeFinished = false) {
  const site = siteFor(data, siteId);
  if (!site) return null;
  const payload = await loadForecast(site.id);
  const result = planFor(data, { siteId: site.id, date, calendarDays: days, includeFinished, forecast: payload?.forecast });
  if (!result) return null;
  const nights: RatedNight[] = result.calendar.map((night) => {
    const forecast = nightForecast(payload?.forecast ?? null, night.dusk, night.dawn);
    return { night, forecast, quality: nightQuality(night, forecast) };
  });
  return { result, payload, nights };
}
