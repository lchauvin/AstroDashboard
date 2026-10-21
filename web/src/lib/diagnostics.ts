// Shapes returned by the engine's diagnostics endpoints (engine/astrodash/diagnostics).

export interface DiagJob {
  id: number;
  revisionId: string;
  session: string;
  status: "queued" | "running" | "done" | "failed";
  phase: string;
  done: number;
  total: number;
  submitted: number;
  started: number | null;
  finished: number | null;
  error: string | null;
}

export interface DiagSummary {
  lights: { key: string; filter: string; frames: number; kept: number; rejected: number; keptSeconds: number; fwhm: number | null }[];
  flats: { key: string; filter: string; verdict: "good" | "warn" | "bad" | null }[];
  calibration: { light: string; status: "ok" | "warn" | "bad" | "missing" }[];
  errors: number;
}

export interface DiagResultRow {
  revisionId: string;
  session: string;
  finished: number;
  duration: number;
  summary: DiagSummary;
  findings?: { code: string; level: "warn" | "bad"; title: string; frames: number }[];
}

export interface Finding {
  code: string;
  level: "info" | "warn" | "bad";
  title: string;
  text: string;
  from: string | null;
  to: string | null;
  frames: string[];
  filters: string[];
}

export interface FrameContext {
  moonAlt: number | null;
  moonSep: number | null;
  moonIllum: number | null;
  dewMargin: number | null;
  focTemp: number | null;
  focPos: number | null;
  pier: string | null;
  az: number | null;
  alt: number | null;
  offsetPx: number | null;
  rotation: number | null;
}

export interface Analysis {
  findings: Finding[];
  flags: Record<string, string[]>;
  events: { kind: "flip" | "focus"; time: string; label: string }[];
  context: Record<string, FrameContext>;
}

export interface FlatMatch {
  light: string;
  filter: string;
  flatSet: string;
  verdict: "ok" | "warn" | "bad" | "unknown";
  dustPixels?: number;
  dustDepth?: number;
  dustInLights?: number | null;
  vignettingRaw?: number;
  vignettingCorrected?: number;
  notes: string[];
}

export interface HorizonData {
  nights: number;
  samples: number;
  obstructed: number;
  bins: { az: number; width: number; blockedMaxAlt: number | null; clearMinAlt: number | null; blocked: number; clear: number; horizon: number | null }[];
  points: { az: number; alt: number; obstructed: boolean }[];
}

export const FLAG_LABELS: Record<string, string> = {
  obstruction: "Obstruction",
  clouds: "Clouds",
  twilight: "Twilight",
  moon: "Moon",
  dew: "Dew",
  focus: "Focus drift",
  tracking: "Tracking",
  mispointed: "Mispointed",
  rotation: "Rotation",
};

export interface SessionIndexRow {
  revisionId: string;
  catalogId: string;
  name: string;
  revision: number;
  stage: string;
  folder: string;
  date: string | null;
  lights: number;
  seconds: number;
  filters: string[];
}

export interface LightFrame {
  file: string;
  path: string;
  mode: string;
  filter_name: string | null;
  exptime: number | null;
  obs_time: string | null;
  altitude_deg: number | null;
  background_median: number | null;
  noise_mad: number | null;
  background_gradient: number | null;
  n_stars: number | null;
  fwhm_median: number | null;
  eccentricity_median: number | null;
  snr_estimate: number | null;
  psf_signal_weight: number | null;
  transparency: number | null;
  relative_snr: number | null;
  region_transparency_min: number | null;
  n_trails: number | null;
  trail_type: string | null;
  settings_note: string | null;
  score: number | null;
  rejected: boolean;
  reasons: string[];
  warnings: string[];
  sunAlt: number | null;
  /** Set when the user overruled astro-eval; its own verdict is then in autoRejected / autoReasons. */
  override?: "keep" | "reject";
  autoRejected?: boolean;
  autoReasons?: string[];
}

/** What a frame's keep / reject button sends: null hands the frame back to astro-eval's verdict. */
export type FrameDecision = "keep" | "reject" | null;

/** astro-eval's own verdict, whatever the user decided. */
export const autoRejected = (f: LightFrame) => (f.override ? Boolean(f.autoRejected) : f.rejected);

/** The decision that leaves `f` rejected (or kept); no override when astro-eval already agrees. */
export const decisionFor = (f: LightFrame, reject: boolean): FrameDecision =>
  reject === autoRejected(f) ? null : reject ? "reject" : "keep";

/** "by hand", plus what astro-eval had said when it rejected the frame. */
export function overrideNote(f: LightFrame): string | null {
  if (!f.override) return null;
  const auto = (f.autoReasons ?? []).map(reasonLabel).join(", ");
  return f.override === "keep" && auto ? `by hand · astro-eval: ${auto}` : "by hand";
}

export interface FlatFrame {
  file: string;
  path: string;
  exptime: number | null;
  obs_time: string | null;
  level_fraction: number | null;
  saturation_fraction: number | null;
  flat_snr: number | null;
  flat_snr_rel: number | null;
  vignetting_depth: number | null;
  score: number | null;
  rejected: boolean;
  reasons: string[];
}

export interface Note {
  level: "ok" | "info" | "warn" | "bad";
  text: string;
}

export interface LightGroupResult {
  key: string;
  filter: string;
  target: string;
  summary: {
    frames: number;
    kept: number;
    rejected: number;
    keptSeconds: number;
    reasons: Record<string, number>;
    median: Record<string, number | null>;
  };
  frames: LightFrame[];
}

export interface FlatSetResult {
  key: string;
  filter: string;
  frames: FlatFrame[];
  summary: {
    n: number;
    kept: number;
    exp: number | null;
    level: number | null;
    snr: number | null;
    masterSnr: number | null;
    settings: string;
    verdict: "good" | "warn" | "bad";
    notes: Note[];
  } | null;
}

export interface DiagResult {
  astroEvalVersion: string;
  configPath: string | null;
  measured: number;
  errors: string[];
  lights: LightGroupResult[];
  flats: FlatSetResult[];
  calibration: {
    rows: { light: string; filter: string; n: number; status: "ok" | "warn" | "bad" | "missing"; flat: string | null; issues: Note[] }[];
    common: Note[];
    orphans: string[];
    counts: Record<string, number>;
  } | null;
  flatMatch?: FlatMatch[];
}

export interface DiagDetail {
  revision: { id: string; folder: string; catalogId: string; name: string };
  session: { folder: string; date: string | null; path: string | null; start: string | null; end: string | null; totals: { lights: number; seconds: number } };
  sessions: string[];
  job: DiagJob | null;
  stored: { finished: number; duration: number; result: DiagResult } | null;
  analysis: Analysis | null;
}

/** astro-eval rejection flag → readable label. */
export const REASON_LABELS: Record<string, string> = {
  high_fwhm: "FWHM",
  high_eccentricity: "Elongated stars",
  low_stars: "Few stars",
  low_snr_weight: "Low SNR weight",
  low_snr: "Low SNR",
  high_noise: "Noise",
  high_background: "Background",
  high_gradient: "Gradient",
  high_residual: "PSF residual",
  low_relative_snr: "Transparency",
  partial_obstruction: "Obstruction",
  airplane_trail: "Plane trail",
  satellite_trail: "Satellite trail",
  low_score: "Low score",
  odd_settings: "Different settings",
  manual: "By hand",
};

export function reasonLabel(r: string): string {
  return REASON_LABELS[r] ?? r.replace(/_/g, " ");
}

/** "NGC 1234 · Ha" / "Ha" astro-eval filter names → the dashboard's letters (Ha → H …). */
export function filterLetter(f: string | null | undefined): string | null {
  if (!f) return null;
  const k = f.toLowerCase();
  if (["ha", "h", "halpha", "h-alpha"].includes(k)) return "H";
  if (["oiii", "o", "o3"].includes(k)) return "O";
  if (["sii", "s", "s2"].includes(k)) return "S";
  if (["l", "lum", "luminance", "clear"].includes(k)) return "L";
  if (["r", "red"].includes(k)) return "R";
  if (["g", "green"].includes(k)) return "G";
  if (["b", "blue"].includes(k)) return "B";
  return f;
}
