// Grouping helpers for the session page: mosaic panels, merged findings, merged calibration rows.

import type { DiagResult, Finding, LightFrame, LightGroupResult, Note } from "@/lib/diagnostics";
import { filterLetter } from "@/lib/diagnostics";
import type { Level } from "@/lib/types";

export type Panel = { id: string; label: string };

/** "M 31 Panel 2" / "PANEL_2" → "Panel 2"; anything else stays as it is. */
export function panelLabel(target: string): string {
  const m = /panel[\s_-]*(\d+)/i.exec(target);
  return m ? `Panel ${m[1]}` : target;
}

/** The mosaic panels of a night, or [] when it is a single field. */
export function panelsOf(groups: LightGroupResult[]): Panel[] {
  const ids = [...new Set(groups.filter((g) => g.target && g.key !== g.filter).map((g) => g.target))];
  if (ids.length < 2) return [];
  const num = (s: string) => Number(/(\d+)\s*$/.exec(panelLabel(s))?.[1] ?? 0);
  return ids.sort((a, b) => num(a) - num(b) || a.localeCompare(b)).map((id) => ({ id, label: panelLabel(id) }));
}

export const RANK: Record<string, number> = { bad: 0, missing: 0, warn: 1, info: 2, ok: 3, good: 3, unknown: 2 };
const AS_LEVEL: Record<string, Level> = { bad: "bad", missing: "bad", warn: "warn", info: "info", unknown: "info" };
/** The most severe of several statuses (bad > warn > info > ok). */
export const worst = (levels: (string | null | undefined)[]): Level =>
  levels.map((l) => AS_LEVEL[l ?? ""] ?? "ok").sort((a, b) => RANK[a] - RANK[b])[0] ?? "ok";

// ---- findings -----------------------------------------------------------------------------

export type FindingGroup = {
  code: string;
  level: Finding["level"];
  title: string;
  items: Finding[];
  texts: string[];
  filters: string[];
  frames: string[];
  /** Frames before the panel scope was applied (to say "5 of 16"). */
  allFrames: number;
  from: string | null;
  to: string | null;
};

const titleOf = (f: Finding) => (f.code === "flat-mismatch" ? "Flats don't match the lights" : f.title);

/** Restrict findings to one panel: keep those touching its frames (or, frameless, its filters). */
export function scopeFindings(findings: Finding[], paths: Set<string> | null, filters: Set<string> | null) {
  if (!paths) return findings.map((f) => ({ f, total: f.frames.length }));
  return findings.flatMap((f) => {
    if (!f.frames.length) return !filters || f.filters.some((x) => filters.has(x)) ? [{ f, total: 0 }] : [];
    const frames = f.frames.filter((p) => paths.has(p));
    return frames.length ? [{ f: { ...f, frames }, total: f.frames.length }] : [];
  });
}

/** One entry per kind of problem: repeated episodes and per-panel duplicates fold together. */
export function groupFindings(scoped: { f: Finding; total: number }[]): FindingGroup[] {
  const map = new Map<string, FindingGroup>();
  for (const { f, total } of scoped) {
    const k = `${f.level === "info" ? "i" : "w"}:${f.code}`;
    const g =
      map.get(k) ??
      ({ code: f.code, level: f.level, title: titleOf(f), items: [], texts: [], filters: [], frames: [], allFrames: 0, from: null, to: null } as FindingGroup);
    g.items.push(f);
    if (!g.texts.includes(f.text)) g.texts.push(f.text);
    for (const x of f.filters) if (!g.filters.includes(x)) g.filters.push(x);
    for (const p of f.frames) if (!g.frames.includes(p)) g.frames.push(p);
    g.allFrames += total;
    if (RANK[f.level] < RANK[g.level]) g.level = f.level;
    if (f.from && (!g.from || f.from < g.from)) g.from = f.from;
    if (f.to && (!g.to || f.to > g.to)) g.to = f.to;
    map.set(k, g);
  }
  const order = ["B", "G", "R", "L", "Ha", "O", "S"];
  const groups = [...map.values()];
  for (const g of groups) g.filters.sort((a, b) => order.indexOf(filterLetter(a) ?? a) - order.indexOf(filterLetter(b) ?? b));
  return groups.sort((a, b) => RANK[a.level] - RANK[b.level] || (a.from ?? "").localeCompare(b.from ?? ""));
}

// ---- per-filter summary -------------------------------------------------------------------

export type FilterSummary = {
  filter: string;
  frames: number;
  kept: number;
  keptSeconds: number;
  reasons: [string, number][];
  fwhm: number | null;
  stars: number | null;
  ecc: number | null;
};

const median = (vals: (number | null)[]) => {
  const v = vals.filter((x): x is number => typeof x === "number" && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};

/** Light groups merged by filter (a mosaic has one group per panel and filter). */
export function byFilter(groups: LightGroupResult[]): FilterSummary[] {
  const map = new Map<string, LightGroupResult[]>();
  for (const g of groups) map.set(g.filter, [...(map.get(g.filter) ?? []), g]);
  return [...map.entries()].map(([filter, gs]) => {
    const reasons = new Map<string, number>();
    for (const g of gs) for (const [k, v] of Object.entries(g.summary.reasons)) reasons.set(k, (reasons.get(k) ?? 0) + v);
    const kept: LightFrame[] = gs.flatMap((g) => g.frames.filter((f) => !f.rejected));
    const single = gs.length === 1 ? gs[0].summary.median : null;
    return {
      filter,
      frames: gs.reduce((s, g) => s + g.summary.frames, 0),
      kept: gs.reduce((s, g) => s + g.summary.kept, 0),
      keptSeconds: gs.reduce((s, g) => s + g.summary.keptSeconds, 0),
      reasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]),
      fwhm: single ? single.fwhm_median : median(kept.map((f) => f.fwhm_median)),
      stars: single ? single.n_stars : median(kept.map((f) => f.n_stars)),
      ecc: single ? single.eccentricity_median : median(kept.map((f) => f.eccentricity_median)),
    };
  });
}

// ---- calibration --------------------------------------------------------------------------

export type CalEntry = {
  filter: string;
  flat: string | null;
  level: Level;
  panels: string[];
  lights: number;
  issues: Note[];
  /** Pixel-level check passed: short notes kept for the tooltip. */
  pixelOk: string[] | null;
};

/** Header check and pixel check side by side, one row per filter when the panels agree. */
export function calibrationEntries(r: DiagResult, keys: Set<string>, panels: Panel[]): CalEntry[] {
  const match = new Map((r.flatMatch ?? []).map((m) => [m.light, m]));
  const rows = (r.calibration?.rows ?? []).filter((row) => keys.has(row.light));
  const out = new Map<string, CalEntry>();
  for (const row of rows) {
    const m = match.get(row.light);
    const issues = row.issues.filter((i) => i.level !== "ok");
    const sizeClash = issues.some((i) => /cannot calibrate/.test(i.text));
    if (m && (m.verdict === "warn" || m.verdict === "bad"))
      for (const n of m.notes) if (!(sizeClash && /cannot be divided/.test(n))) issues.push({ level: m.verdict, text: n });
    const level = worst([row.status, ...issues.map((i) => i.level)]);
    const sig = [row.filter, row.flat, level, ...issues.map((i) => i.text)].join("|");
    const panel = panels.find((p) => row.light.startsWith(p.id))?.label;
    const e = out.get(sig) ?? { filter: row.filter, flat: row.flat, level, panels: [], lights: 0, issues, pixelOk: m?.verdict === "ok" ? [] : null };
    if (panel && !e.panels.includes(panel)) e.panels.push(panel);
    e.lights += row.n;
    if (e.pixelOk && m?.verdict === "ok") e.pixelOk.push(`${panel ? panel + ": " : ""}${m.notes.join(" ")}`);
    out.set(sig, e);
  }
  return [...out.values()];
}
