/** Light-leak finder (engine: astrodash/leaks.py, api_leaks.py). */

import type { Level } from "./types";

export type DarkSetting = {
  exposure: number | null;
  setTemp: number | null;
  readout: string | null;
  gain: number | null;
  offset: number | null;
  binning: number | null;
  filter?: string | null;
};

export type DarkGroup = DarkSetting & {
  folder: string;
  kind: string;
  instrume: string | null;
  frames: number;
  first?: string | null;
  last?: string | null;
  build?: string;
  /** In NINA's image folder, where auto-ingest may take its lights (browse results). */
  inDownload?: boolean;
};

export type Structure = {
  level: number;
  gradient: { dx: number; dy: number; amp: number; side: string };
  edges: Record<Side, number>;
  zones: number[][];
  residualRms: number;
  background: number;
  rows: number[];
  cols: number[];
};

export type Side = "top" | "bottom" | "left" | "right";
export const SIDES: Side[] = ["top", "bottom", "left", "right"];

export type FrameRow = {
  file: string;
  time: string | null;
  ccdTemp: number | null;
  median: number;
  level: number;
  mean: number;
  std: number;
  noise: number;
};

export type SetResult = {
  frames: number;
  available: number;
  width: number;
  height: number;
  settings: DarkSetting & { instrume: string | null };
  first: string | null;
  last: string | null;
  ccdTemp: { median: number; min: number; max: number } | null;
  median: number;
  level: number;
  mean: number;
  std: number;
  noise: number;
  drift: { adu: number; hours: number; z: number | null; significant: boolean } | null;
  structure: Structure;
  halfNoise: { px: number; cell: number; smooth: number };
  perFrame: FrameRow[];
};

export type Verdict = "better" | "worse" | "mixed" | "changed" | "same" | "error";

export type DarkSet = {
  id: string;
  folder: string;
  want: DarkSetting;
  comment: string;
  added: number;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  result: SetResult | null;
  analysedAt?: number;
  progress: { done: number; total: number } | null;
  vsPrevious?: { baseId: string; verdict: Verdict; headline: string; levelDelta?: number; findings?: number };
};

export type SpotVerdict = "leak" | "step" | "tight" | "reference";

export type SpotFrame = {
  index: number;
  file: string;
  time: string | null;
  ccdTemp: number | null;
  level: number;
  noise: number;
  std: number;
  mean: number;
  median: number;
  levelDelta: number;
  noiseDelta: number;
  structure: number;
  structureRatio: number | null;
  levelSignificant: boolean;
  noiseSignificant: boolean;
  structureSignificant: boolean;
  verdict: SpotVerdict;
  where: { side: string; gradient: number; zone: string; zones: number[][] } | null;
  inReference: boolean;
  limit: number;
};

export type SpotEvaluation = {
  reference: { frames: number[]; auto: boolean; level: number; noise: number; levelScatter: number | null; noiseScatter: number | null };
  thresholds: { level: number; noise: number; structureRatio: number; structureCell: number };
  counts: Record<SpotVerdict, number>;
  sharedLimit: number;
  frames: SpotFrame[];
};

export type SpotCheck = {
  id: string;
  comment: string;
  folder: string;
  want: DarkSetting;
  referenceFolder: string | null;
  frames: { file: string; time: string | null; ccdTemp: number | null; role: "reference" | "probe"; comment: string }[];
  autoReference: boolean;
  added: number;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  analysedAt?: number;
  progress: { done: number; total: number } | null;
  evaluation?: SpotEvaluation;
};

export const SPOT: Record<SpotVerdict, { label: string; level: Level }> = {
  leak: { label: "Light gets in", level: "bad" },
  step: { label: "Bias step, no light", level: "info" },
  tight: { label: "Tight", level: "ok" },
  reference: { label: "Reference", level: "info" },
};

export type LeakTest = { id: string; name: string; notes: string; created: number; sets: DarkSet[]; checks: SpotCheck[] };

export type LeakTestSummary = {
  id: string;
  name: string;
  created: number;
  sets: number;
  checks: number;
  lastComment: string | null;
  updated: number;
};

export type Finding = { level: Level; text: string };

export type Comparison = {
  base: string;
  comp: string;
  verdict: Verdict;
  headline: string;
  findings: Finding[];
  notes: Finding[];
  level: { base: number; comp: number; delta: number; z: number | null; significant: boolean };
  noise: { base: number; comp: number; delta: number; z: number | null; significant: boolean };
  diff: Structure & {
    gradient: Structure["gradient"] & { z: number | null; significant: boolean };
    edgeZ: Record<Side, number | null>;
    edgeSignificant: Record<Side, boolean>;
    residualRatio: number | null;
    residualSignificant: boolean;
    noise: { px: number; cell: number; smooth: number };
  };
  display: { lo: number; hi: number; loCentered: number; hiCentered: number; structureLimit: number; fitLimit: number; absoluteLimit: number };
};

export const VERDICT: Record<Verdict, { label: string; level: Level }> = {
  better: { label: "Better", level: "ok" },
  worse: { label: "Worse", level: "bad" },
  mixed: { label: "Mixed", level: "warn" },
  changed: { label: "Changed", level: "warn" },
  same: { label: "No difference", level: "info" },
  error: { label: "Not comparable", level: "bad" },
};

export function adu(v: number | null | undefined, digits = 2, signed = false): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toFixed(digits);
  if (Number(s) === 0) return s; // no sign on a value that rounds to zero
  return `${signed ? (v > 0 ? "+" : "−") : v < 0 ? "−" : ""}${s}`;
}

export function settingText(s: DarkSetting): string {
  const parts: string[] = [];
  if (s.filter) parts.push(s.filter);
  if (s.exposure != null) parts.push(s.exposure >= 1 ? `${s.exposure} s` : `${s.exposure * 1000} ms`);
  if (s.setTemp != null) parts.push(`${s.setTemp} °C`);
  if (s.readout) parts.push(s.readout);
  if (s.gain != null || s.offset != null) parts.push(`gain ${s.gain ?? "?"} · offset ${s.offset ?? "?"}`);
  if (s.binning != null && s.binning !== 1) parts.push(`bin ${s.binning}`);
  return parts.join(" · ");
}

/** Short label for a set: its number in the test, then its comment. */
export function setLabel(t: LeakTest, id: string): string {
  const i = t.sets.findIndex((s) => s.id === id);
  const s = t.sets[i];
  return s ? `#${i + 1}${s.comment ? ` · ${s.comment}` : ""}` : "?";
}
