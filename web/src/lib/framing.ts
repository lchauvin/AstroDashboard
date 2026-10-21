// Shapes returned by the engine's framing endpoints (engine/astrodash/api_framing.py), and their wording.
// A night's framing is compared with the previous night's: the largest corner shift (shift and
// rotation together) against the nights' dither spread.

import { shortDate } from "./format";
import type { Level } from "./types";

export type FramingLevel = "ok" | "warn" | "bad";

export interface FramingVerdict {
  /** ready: compared · pending: stars being measured · first: no earlier night · unmatched: no stars in common with
   *  the previous night · unmeasured: no frame with enough stars · no-target: Live frame not matched to a target. */
  status: "ready" | "pending" | "first" | "unmatched" | "unmeasured" | "no-target";
  level?: FramingLevel;
  previous?: string;
  previousDate?: string | null;
  /** Largest distance between the same corner of the two framings (sensor px). */
  cornerPx?: number;
  shiftPx?: number;
  /** Degrees, folded into ±90° (a meridian flip is not a rotation). */
  rotation?: number;
  tolerancePx?: number;
  frameTolerancePx?: number;
  spreadPx?: number;
  previousSpreadPx?: number;
  matches?: number;
  /** Lights measured that night, those framed differently from the rest, those whose stars did not match. */
  frames?: number;
  outliers?: number;
  failed?: number;
}

export interface FramingJob {
  label: string;
  status: "queued" | "running" | "done" | "failed";
  done: number;
  total: number;
  error: string | null;
}

export interface RevisionFraming {
  revisionId: string;
  panels: string[];
  /** Night folder → mosaic panel ("" without panels) → verdict. */
  sessions: Record<string, Record<string, FramingVerdict>>;
  job: FramingJob | null;
  minTolerancePx: number;
}

export interface FrameFraming {
  cornerPx: number;
  level: FramingLevel;
  /** Framed differently from the rest of its own night. */
  outlier: boolean;
}

export interface SessionFraming {
  revisionId: string;
  session: string;
  /** Frames keyed like Diagnostics' frame paths (relative to the data root). */
  panels: { panel: string | null; verdict: FramingVerdict; frames: Record<string, FrameFraming> }[];
  job: FramingJob | null;
  minTolerancePx: number;
}

export interface LiveFraming extends FramingVerdict {
  frame?: string;
  revisionId?: string | null;
  target?: string | null;
  panel?: string | null;
  job?: FramingJob | null;
  error?: string;
}

/** Still worth asking the engine again (its job is measuring stars). */
export const framingBusy = (job: FramingJob | null | undefined) => !!job && job.status !== "failed";

export const px = (v: number | null | undefined) => (v == null ? "?" : `${Math.round(v)} px`);
const deg = (v: number | null | undefined) => (v == null ? "?" : `${Math.abs(v).toFixed(Math.abs(v) < 1 ? 2 : 1)}°`);

/** Status icon level; null when there is nothing to judge (first night, still measuring…). */
export function framingLevel(v: FramingVerdict | null | undefined): Level | null {
  if (!v) return null;
  if (v.status === "ready") return v.level ?? null;
  return v.status === "unmatched" ? "warn" : null;
}

const night = (v: FramingVerdict) => (v.previousDate ? shortDate(v.previousDate) : (v.previous ?? "the previous night"));

/** One sentence: the verdict and the numbers behind it. */
export function framingText(v: FramingVerdict): string {
  switch (v.status) {
    case "pending":
      return "Measuring the stars…";
    case "first":
      return "First night: the reference for the next one";
    case "unmeasured":
      return "No frame with enough stars to tell (clouds?)";
    case "unmatched":
      return `No stars in common with ${night(v)}: a different field, or too few stars`;
    case "no-target":
      return "Not matched to a target in the library";
    default:
      if (v.level === "ok") return `Aligned with ${night(v)}: ${px(v.cornerPx)} at the corners, within the ${px(v.tolerancePx)} dither`;
      if (v.level === "warn") return `A little off ${night(v)}: ${px(v.cornerPx)} at the corners, beyond the ${px(v.tolerancePx)} dither`;
      return `Off ${night(v)}: ${px(v.cornerPx)} at the corners, over twice the ${px(v.tolerancePx)} dither`;
  }
}

/** The numbers, for a tooltip or a detail line. */
export function framingDetails(v: FramingVerdict, minTolerancePx = 10): string[] {
  const out: string[] = [];
  if (v.status === "ready") {
    out.push(`Shifted ${px(v.shiftPx)}, rotated ${deg(v.rotation)} from ${v.previous}`);
    out.push(
      `Dither spread ${px(v.spreadPx)} this night, ${px(v.previousSpreadPx)} the night before` +
        ((v.tolerancePx ?? 0) <= minTolerancePx ? ` (tolerance never below ${px(minTolerancePx)})` : ""),
    );
  }
  if (v.frames != null && v.status !== "pending") {
    out.push(
      `${v.frames} light${v.frames === 1 ? "" : "s"} measured` +
        (v.outliers ? ` · ${v.outliers} framed differently from the rest of the night` : "") +
        (v.failed ? ` · ${v.failed} without matching stars` : ""),
    );
  }
  return out;
}
