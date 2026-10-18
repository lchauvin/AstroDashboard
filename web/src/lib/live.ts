/** Live payloads: the engine's own NINA + PHD2 connection (PLAN §5.3), and auto-ingest. */

import type { Level } from "./types";
export type { Level };

export type DeviceFact = { label: string; value: string; advanced: boolean; level?: Level };
export type DeviceCard = { key: string; label: string; name: string | null; status: string; level: Level | null; facts: DeviceFact[] };

export type SeqStep = {
  id: string;
  name: string;
  status: string;
  depth: number;
  container: boolean;
  parent: string | null;
  info: { ExposureTime?: number; ExposureCount?: number; Iterations?: number; CompletedIterations?: number; Filter?: string; Gain?: number; Offset?: number; ImageType?: string; Binning?: string };
};

export type Sequence = {
  available: boolean;
  status: "running" | "finished" | "idle" | "none";
  steps: SeqStep[];
  path: string[];
  current: SeqStep | null;
  target: string | null;
  done: number;
  total: number;
  error?: string;
};

export type Frame = {
  index: number;
  date: string | null;
  filename: string | null;
  target: string | null;
  type: string | null;
  filter: string | null;
  exposure: number | null;
  hfr: number | null;
  stars: number | null;
  temperature: number | null;
  gain: number | null;
  median: number | null;
  rms: string | null;
  flags: string[];
};

export type AxisStats = { rms: number; peak: number; mean: number; lag1: number; idle: number; flips: number; pulseMs: number; limited: number; pulsesKnown: boolean };
export type GuideStats = { n: number; seconds: number; total: number; se: number; ra: AxisStats; dec: AxisStats; snr: number; mass: number; hfdPx: number; oscillation: number };
export type AxisVerdict = { axis: "ra" | "dec"; verdict: string; title: string; hint: string; evidence: string };
export type Diagnosis = { axes: AxisVerdict[]; caveats: string[]; actionable: boolean; axisTarget?: number };

export type GuideGraph = {
  t: number[];
  ra: number[];
  dec: number[];
  raMs: number[];
  decMs: number[];
  snr: number[];
  excluded: boolean[];
  events: { t: number; label: string }[];
};

/** The sequence guard (engine `guard.py`): stops the NINA sequence while the guide star is lost, resumes it after steady guiding. */
export type GuardState = {
  enabled: boolean;
  stopAfterS: number;
  resumeAfterS: number;
  maxWaitMin: number;
  state: "off" | "watching" | "lost" | "paused";
  error: string | null;
  pausedAt: number | null;
  lostForS: number | null;
  steadyForS: number | null;
  giveUpAt: number | null;
  log: { t: number; message: string }[];
};

/** Polar alignment read from PHD2's Dec corrections (engine `polar.py`). Rates are the pointing's Dec drift, ″/min, + = north. */
export type PolarFit =
  | { ok: false; reason: string }
  | { ok: true; alt: number; az: number; altSe: number; azSe: number; total: number; sigma: number; spanH: number; n: number; pierFlipped: boolean; latKnown: boolean; directions: boolean };
export type PolarShift = { t: number; before: number; after: number; delta: number; deltaArcmin: number; epoch: number };
export type Polar = {
  now: { rate: number; minArcmin: number; windows: number; ha: number | null } | null;
  fit: PolarFit;
  advice: string[];
  shifts: PolarShift[];
  windows: number;
  status: string | null;
  caveats: string[];
  series: { t: number[]; rate: number[]; model: (number | null)[] };
  windowS: number;
};

export type Phd2 = {
  connected: boolean;
  address: string;
  error: string | null;
  version?: string | null;
  appState?: string;
  pixelScale?: number | null;
  exposureMs?: number | null;
  exposureChoices?: number[];
  decGuideMode?: string | null;
  calibrated?: boolean | null;
  calibration?: { xAngle?: number; yAngle?: number; xRate?: number; yRate?: number; declination?: number } | null;
  profile?: string | null;
  equipment?: Record<string, { name?: string; connected?: boolean }>;
  algorithms?: Record<string, string>;
  params?: Record<string, Record<string, number>>;
  settling?: boolean;
  paused?: boolean;
  guidingForS?: number | null;
  lastAlert?: { type?: string; msg?: string; at?: number } | null;
};

export type Suggestion = { id: string; axis: "ra" | "dec"; param: string; current: number; value: number; why: string; note: string | null; status: "open" | "applied" | "dismissed" | "stale" };
export type Brief = { total: number; ra: number; dec: number; n: number; se: number; hfdPx: number };
export type Analysis = {
  id: string;
  t: number;
  model: string;
  effort?: string;
  manual: boolean;
  latencyMs?: number;
  assessment?: string;
  suggestions: Suggestion[];
  notes: string[];
  /** With auto-apply on: what happened to the top suggestion. */
  auto?: string;
  diagnosis: Diagnosis;
  stats: Brief | null;
  error?: string;
};
export type Change = {
  id: string;
  t: number;
  axis: string;
  param: string;
  old: number;
  new: number;
  source: "manual" | "ai" | "revert" | "auto" | "auto-revert";
  reverted?: boolean;
  before: Brief | null;
  after: Brief | null;
  verdict: "pending" | "better" | "worse" | "neutral" | "void";
  outcome: string;
  measureAt: number;
};

export type LiveState = {
  simulated: boolean;
  serverTime: number;
  /** clockOffset: the observatory PC's clock minus this machine's, in seconds (NINA's times are corrected for it). */
  nina: { connected: boolean; error: string | null; lastOk: number | null; url: string | null; websocket: boolean; clockOffset: number };
  phd2: Phd2;
  equipment: DeviceCard[];
  lost: { key: string; label: string; name: string | null; since: number }[];
  sequence: Sequence;
  frames: Frame[];
  latest: { source: "file" | "nina"; name: string | null; token: string; index?: number; record: Frame | null } | null;
  /** While the camera is connected: seconds left of the exposure in progress (null between frames) and the camera's state. */
  exposure: { left: number | null; total: number | null; state: string } | null;
  guiding: { stats: GuideStats | null; diagnosis: Diagnosis; graph: GuideGraph; active: string[]; scale: number | null };
  advisor: {
    enabled: boolean;
    model: string;
    effort: string;
    autoApply: boolean;
    intervalMin: number;
    targetRms: number;
    key: { ok: boolean; message: string };
    busy: boolean;
    lastError: string | null;
    check: { t: number; message: string } | null;
    analyses: Analysis[];
    changes: Change[];
  };
  paramInfo: Record<string, { label: string; help: string; basic: boolean }>;
  /** Absent from engines older than the polar-alignment estimate. */
  polar?: Polar;
  /** Absent from engines older than the sequence guard. */
  guard?: GuardState;
};

// ---------------------------------------------------------------- auto-ingest

export type IngestAction = {
  op: "move" | "copy";
  src: string;
  dst: string;
  night: string;
  download_night: string;
  kind: string;
  filter: string | null;
  revision_id: string;
  target: string;
  size: number;
};

export type IngestHold = {
  src: string;
  download_night: string;
  reason: string;
  message: string;
  kind: string | null;
  object: string | null;
  filter: string | null;
  prompt: boolean;
};

/** folder: false when the night's frames lie directly under .Download (grouped by their header night). */
/** ingested: copies in .Download of frames already in their session (re-synced, e.g. by robocopy). */
export type IngestNightPlan = { lights: number; flats: number; ingested?: number; closed: boolean; newestLight: string | null; emptyDirs?: number; folder?: boolean };

/** Where the lights of one NINA object, still in .Download, would go (per header night). */
export type IngestObject = { object: string | null; frames: number; revisionId: string | null; target: string | null; stage: string | null; how: "tonight" | "follow" | "mapping" | "name" | ""; problem: string };

/** Tonight's target chosen in Live: every light of the night, and/or per NINA object (keys are casefolded). */
export type IngestAssignment = { all: string | null; objects: Record<string, string>; names: Record<string, string>; setAt?: number };

export type IngestTarget = { id: string; folder: string; catalogId: string; name: string; stage: string; revision: number };

export type IngestJournalNight = { moved: number; copied: number; undone: number; errors: number; targets: Record<string, number>; last: number; emptyDirs: number };

export type IngestStatus = {
  mode: "off" | "dry-run" | "auto";
  lastRun: { at: number; mode: string; moved: number; copied: number; errors: number } | null;
  error: string | null;
  downloadRoot: string;
  downloadExists: boolean;
  plan: {
    actions: IngestAction[];
    holds: IngestHold[];
    skipped: IngestHold[];
    nights: Record<string, IngestNightPlan>;
    objects: Record<string, Record<string, IngestObject>>;
    /** Finished targets about to receive frames (following NINA with "also finished" on): reopened first. */
    reopen: { revisionId: string; target: string; stage: string }[];
    scannedAt: number;
  } | null;
  /** Follow NINA's target (each NINA target → its library target), and whether finished targets are allowed (and reopened). */
  follow: boolean;
  followFinished: boolean;
  /** What NINA is imaging now and where following it sends frames; blockedBy names a finished target not switched to. */
  nina: { object: string; revisionId: string | null; blockedBy: string | null; target: string | null; stage: string | null; problem: string; running: boolean } | null;
  /** Status changes written to target.json (reopened finished targets, "mark finished again"). */
  reopened: { ts: number; revisionId: string; folder: string; from: string; to: string }[];
  /** The night in progress (evening date, noon to noon), and the targets chosen per night. */
  tonight: string;
  assignments: Record<string, IngestAssignment>;
  /** Lights already moved into tonight's NIGHT_ folders, per revision id. */
  tonightMoved: Record<string, number>;
  closed: string[];
  /** Nights you undid: Auto leaves them alone until resumed. */
  paused: string[];
  journal: Record<string, IngestJournalNight>;
  recent: { id: number; ts: number; downloadNight: string; op: string; src: string; dst: string; revision: string; kind: string; filter: string | null; undone: number | null; error: string | null }[];
  objectMap: { object: string; catalogId: string | null; revisionId: string | null; learned: boolean; frames?: number }[];
  targets: IngestTarget[];
};

export const fileName = (p: string) => p.split(/[\\/]/).pop() ?? p;

const FILTER_NAME_TO_LETTER: Record<string, string> = {
  ha: "H", h: "H", halpha: "H", "h-alpha": "H", oiii: "O", o3: "O", o: "O", sii: "S", s2: "S", s: "S",
  l: "L", lum: "L", luminance: "L", clear: "L", r: "R", red: "R", g: "G", green: "G", b: "B", blue: "B",
};

/** NINA filter name ("Ha", "OIII", "Lum") → the dashboard's letter, so colours match the Library. */
export const filterLetterOf = (name: string | null | undefined) => (name ? FILTER_NAME_TO_LETTER[name.trim().toLowerCase()] ?? name : null);

export const fmt = (v: number | null | undefined, d = 1, unit = "") => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(d)}${unit}`);

export function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  if (m < 1) return `${Math.round(s)} s`;
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function ago(t: number | null | undefined, now = Date.now() / 1000): string {
  if (!t) return "—";
  const s = Math.max(0, now - t);
  return s < 60 ? `${Math.round(s)} s ago` : `${duration(s)} ago`;
}

export const clock = (t: number | string | null | undefined) =>
  t == null ? "—" : new Date(typeof t === "number" ? t * 1000 : t).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });

/** POST JSON to the engine; throws with the engine's own message. */
export async function enginePost<T = unknown>(path: string, body?: unknown, method = "POST"): Promise<T> {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data as T;
}
