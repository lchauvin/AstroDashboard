import "server-only";

import { engineGet, type EngineResult } from "@/lib/engine";
import { calibrateSites, type SiteCalibration } from "@/lib/planning/calibrate";
import { nightForecast, type Forecast, type ForecastPayload, type NightForecast } from "@/lib/planning/forecast";
import { matchInput, plan, type CalendarNight, type PlanResult } from "@/lib/planning/plan";
import { nightQuality, type NightQuality } from "@/lib/planning/quality";
import { buildRig, type PlanRig } from "@/lib/planning/rig";
import type { CatalogInfo } from "@/lib/planning/target";
import { defaultSite } from "@/lib/planning/sites";
import type { MatchRevision, PlanTarget, PlanningContext, SkyPayload } from "@/lib/planning/types";
import { findCatalog, ideaCandidates } from "./catalog";
import { loadTelescopiusLists } from "./telescopius";
import { classifyType } from "@/lib/snr";
import type { ListTarget, TargetList } from "@/lib/telescopius";
import type { GoalRow, MatchCandidate } from "@/components/planning/GoalsEditor";
import { FILTER_NAMES, type FilterLetter } from "@/lib/planning/rig";

/**
 * Everything Planning needs from the engine, assembled on the server:
 * the planning context (targets, sites, settings, detected rig), the measured
 * sky (→ per-site calibration), and the Match measurements per target (frames
 * not measured yet are queued in the engine and count on a later load).
 */

export type PlanningData = {
  context: PlanningContext;
  rig: PlanRig;
  calibration: Record<string, SiteCalibration>;
  match: Record<string, MatchRevision | null>;
  catalogFor: (t: PlanTarget) => CatalogInfo | null;
  /** Targets from your Telescopius lists, as catalog entries (the vendored catalog's when it knows them). */
  listed: CatalogInfo[];
  lists: TargetList[];
  listsError: string | null;
};

/** A Telescopius list target as a catalog entry: ours when the vendored catalog knows it, else built from the list. */
function listInfo(t: ListTarget): CatalogInfo {
  return (
    findCatalog(t.id, t.name) ?? {
      name: t.id,
      ra: t.ra,
      dec: t.dec,
      majAxArcmin: t.sizeArcmin,
      minAxArcmin: null,
      paDeg: t.paDeg,
      type: "",
      vmag: t.vmag,
      surfBr: null,
      commonName: t.name !== t.id ? t.name : null,
      objectClass: classifyType("", t.id),
    }
  );
}

/** The idea pool: the catalog's candidates plus your listed targets (no duplicates). */
function ideaPool(data: PlanningData): CatalogInfo[] {
  const seen = new Set(ideaCandidates().map((i) => i.name.toLowerCase()));
  return [...ideaCandidates(), ...data.listed.filter((i) => !seen.has(i.name.toLowerCase()))];
}

const listedNames = (data: PlanningData) => new Set(data.listed.map((i) => i.name.toLowerCase()));

export async function loadPlanningData(): Promise<EngineResult<PlanningData>> {
  const [ctx, sky, lists, match] = await Promise.all([
    engineGet<PlanningContext>("/api/planning/context"),
    engineGet<SkyPayload>("/api/planning/sky"),
    loadTelescopiusLists(),
    engineGet<{ revisions: Record<string, MatchRevision | null> }>("/api/match/summary"),
  ]);
  if (!ctx.ok) return ctx;
  const context = ctx.data;
  const rig = buildRig(context.detectedRig, context.settings.rig);
  const calibration = sky.ok ? calibrateSites(sky.data.samples, context.sites, rig) : {};

  const catalogFor = (t: PlanTarget) => findCatalog(t.catalogId, t.name);
  const listed = new Map<string, CatalogInfo>();
  for (const l of lists?.lists ?? []) for (const t of l.targets) {
    const info = listInfo(t);
    listed.set(info.name.toLowerCase(), info);
  }
  return {
    ok: true,
    data: {
      context,
      rig,
      calibration,
      match: match.ok ? match.data.revisions : {},
      catalogFor,
      listed: [...listed.values()],
      lists: lists?.lists ?? [],
      listsError: lists?.error ?? null,
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
    ideas: ideaPool(data),
    listed: listedNames(data),
    match: data.match,
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
  const ideaName = key.startsWith("idea:") ? key.slice(5) : null;
  const idea = !target && ideaName ? (findCatalog(ideaName) ?? data.listed.find((i) => i.name === ideaName) ?? null) : null;
  if (!target && !idea) return null;
  const result = plan({
    site,
    date,
    rig: data.rig,
    targets: target ? [target] : [],
    catalogFor: data.catalogFor,
    ideas: idea ? [idea] : [],
    listed: listedNames(data),
    match: data.match,
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

/**
 * Targets with Match data in a filter, as reference candidates (`exclude`: the target itself). Most similar in that
 * filter first (closest quality per hour, on a ratio scale: ×2 and ×0.5 are equally far); newest first while the
 * target itself is not measured in the filter.
 */
function candidatesFor(data: PlanningData, letter: FilterLetter, exclude: string): MatchCandidate[] {
  const own = data.match[exclude]?.filters[letter]?.qPerHour ?? 0;
  const distance = (c: MatchCandidate) => (own > 0 && c.measured ? Math.abs(Math.log(c.measured.qPerHour / own)) : 0);
  return data.context.targets
    .filter((t) => t.id !== exclude && (data.match[t.id]?.filters[letter]?.qPerHour ?? 0) > 0)
    .map((t) => {
      const m = data.match[t.id]!;
      const f = m.filters[letter]!;
      const tot = t.filters.find((x) => x.filter === letter);
      return { id: t.id, title: t.catalogId, subtitle: t.name, goodHours: (tot?.goodSeconds ?? 0) / 3600, measured: f, scale: m.scale, nights: f.nights, lastNight: t.lastNight };
    })
    .sort((a, b) => distance(a) - distance(b) || (b.lastNight ?? "").localeCompare(a.lastNight ?? "") || a.title.localeCompare(b.title));
}

/** Goal editor rows for a planned library target (both the Planning detail page and the Library target page). */
export function goalRowsFor(data: PlanningData, one: NonNullable<ReturnType<typeof planOne>>): GoalRow[] {
  const { plan: p } = one;
  const target = p.target;
  if (!target) return [];
  return p.relevant.map((l) => {
    const tot = target.filters.find((f) => f.filter === l);
    const m = matchInput(target, l, data.context.targets, data.match);
    return {
      letter: l,
      name: FILTER_NAMES[l],
      goal: target.goals[l] ?? null,
      goodSeconds: tot?.goodSeconds ?? 0,
      rawSeconds: tot?.seconds ?? 0,
      measured: m?.measured ?? null,
      scale: m?.scale ?? null,
      candidates: candidatesFor(data, l, target.id),
    };
  });
}

/** What the engine is still measuring for Match on this target or the references its goals use, for a note. */
export function matchMeasuring(data: PlanningData, target: PlanTarget): string | null {
  const ids = [target.id, ...Object.values(target.goals).flatMap((g) => (g.mode === "match" && g.ref ? [g.ref] : []))];
  const busy = ids.map((id) => data.match[id]).filter((m): m is MatchRevision => !!m?.pending);
  if (!busy.length) return null;
  return busy.map((m) => (m.job && m.job.total ? `${m.job.label} (${m.job.done}/${m.job.total})` : m.job?.label ?? m.revisionId)).join(", ");
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
