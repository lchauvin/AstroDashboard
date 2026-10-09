import "server-only";

import { engineGet, engineSend, type EngineResult } from "@/lib/engine";
import { calibrateSites, type SiteCalibration } from "@/lib/planning/calibrate";
import { halphaKey, plan, type PlanResult } from "@/lib/planning/plan";
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

export function planFor(
  data: PlanningData,
  opts: { siteId?: string | null; date: string; includeFinished?: boolean; seasonDays?: number; calendarDays?: number },
): PlanResult | null {
  const sites = data.context.sites;
  const site = sites.find((s) => s.id === opts.siteId) ?? defaultSite(sites);
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
  });
}

/** One target (library revision id, or "idea:<catalog name>") planned at one site; null when unknown. */
export function planOne(data: PlanningData, key: string, siteId: string | null | undefined, date: string, seasonDays = 365) {
  const sites = data.context.sites;
  const site = sites.find((s) => s.id === siteId) ?? defaultSite(sites);
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
