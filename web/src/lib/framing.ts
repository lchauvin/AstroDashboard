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
  /** How far this night's frames sit from its framing (dither and drift), and the night before's. */
  spreadPx?: number;
  previousSpreadPx?: number;
  /** This night's dither (the spread of its shifts alone) and how much its rotation changed (°). */
  ditherPx?: number;
  rotationDeg?: number;
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
  /** Against the previous night's framing (absent on a first night). */
  cornerPx?: number;
  level?: FramingLevel;
  /** Framed differently from the rest of its own night. */
  outlier: boolean;
  /** Against its own night's framing: largest corner shift, shift of the centre (px), rotation (° and px at the corners, signed). */
  nightPx?: number;
  nightShiftPx?: number;
  nightRotation?: number;
  nightRotationPx?: number;
}

/** How a night's framing moved: the spread of its frames (dither and drift), of their shifts alone (dither: it
 *  never rotates the frame), and how much the rotation changed (drift, or a meridian flip's step). */
export interface NightCourse {
  spreadPx: number;
  ditherPx: number;
  rotationDeg: number;
  rotationPx: number;
  frames: number;
}

export interface SessionFraming {
  revisionId: string;
  session: string;
  /** Frames keyed like Diagnostics' frame paths (relative to the data root). */
  panels: { panel: string | null; verdict: FramingVerdict; frames: Record<string, FrameFraming>; night?: NightCourse | null }[];
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
export const deg = (v: number | null | undefined) => (v == null ? "?" : `${Math.abs(v).toFixed(Math.abs(v) < 1 ? 2 : 1)}°`);

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
      if (v.level === "ok") return `Aligned with ${night(v)}: ${px(v.cornerPx)} at the corners, within the nights' ${px(v.tolerancePx)} spread`;
      if (v.level === "warn") return `A little off ${night(v)}: ${px(v.cornerPx)} at the corners, beyond the nights' ${px(v.tolerancePx)} spread`;
      return `Off ${night(v)}: ${px(v.cornerPx)} at the corners, over twice the nights' ${px(v.tolerancePx)} spread`;
  }
}

/** The numbers, for a tooltip or a detail line. */
export function framingDetails(v: FramingVerdict, minTolerancePx = 10): string[] {
  const out: string[] = [];
  if (v.status === "ready") {
    out.push(`Shifted ${px(v.shiftPx)}, rotated ${deg(v.rotation)} from ${v.previous}`);
    out.push(
      `Spread ${px(v.spreadPx)} this night (dither ${px(v.ditherPx)}, rotation changed ${deg(v.rotationDeg)}), ${px(v.previousSpreadPx)} the night before` +
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
