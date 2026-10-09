// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * The CCD equation, and the three questions worth asking of it.
 *
 * For N subs of length t, average-combined, total integration T = N.t:
 *
 *   SNR(T) = T.e_obj / sqrt( T.(e_obj + e_sky + e_dark) + (T/t).R^2 )
 *
 * which simplifies to
 *
 *   SNR(T) = sqrt(T) . e_obj / sqrt( e_obj + e_sky + e_dark + R^2/t )
 *
 * so at a FIXED sub length the curve is exactly proportional to sqrt(T). Read
 * noise does not bend the curve — it lowers the coefficient. That distinction
 * matters: doubling integration always buys the same 1.41x whatever your subs
 * are, but choosing subs too short permanently costs you a fraction of every
 * hour you then spend. `readNoisePenalty` is the number that makes that cost
 * visible, and `optimalSubLength` is how you avoid paying it.
 */

import type { Camera, ExposureParams } from "./types";

/** Total noise variance per second of integration, e-^2. */
function noisePerSecond(p: ExposureParams): number {
  return p.objectRate + p.skyRate + p.darkRate + (p.subSeconds > 0 ? (p.readNoise * p.readNoise) / p.subSeconds : 0);
}

/** SNR for a total integration time in seconds. */
export function snrAt(p: ExposureParams, totalSeconds: number): number {
  if (totalSeconds <= 0 || p.objectRate <= 0) return 0;
  const variance = noisePerSecond(p);
  if (variance <= 0) return 0;
  return (Math.sqrt(totalSeconds) * p.objectRate) / Math.sqrt(variance);
}

/**
 * Total integration in seconds needed to reach `targetSnr`.
 * Closed form, and exactly the inverse of `snrAt`.
 */
export function secondsForSnr(p: ExposureParams, targetSnr: number): number {
  if (p.objectRate <= 0 || targetSnr <= 0) return Infinity;
  return (targetSnr * targetSnr * noisePerSecond(p)) / (p.objectRate * p.objectRate);
}

export const hoursForSnr = (p: ExposureParams, targetSnr: number): number =>
  secondsForSnr(p, targetSnr) / 3600;

/**
 * How much SNR you lose to read noise, as a factor >= 1.
 *
 * 1.00 means the subs are long enough that read noise is irrelevant; 1.20 means
 * you need 1.2^2 = 1.44x the total integration to match what you would get with
 * ideal subs. This is the honest cost of a too-short sub length.
 */
export function readNoisePenalty(p: ExposureParams): number {
  const shotOnly = p.objectRate + p.skyRate + p.darkRate;
  if (shotOnly <= 0) return Infinity;
  return Math.sqrt(noisePerSecond(p) / shotOnly);
}

/**
 * Shortest sub that keeps read noise negligible.
 *
 * The classic criterion: make the sky (plus dark) shot-noise variance at least
 * `factor` times the read noise variance. factor = 10 is the usual choice and
 * corresponds to a ~2.4% SNR penalty.
 *
 * Returns Infinity when the background is so faint that no practical sub gets
 * there — which is the real answer at 3 nm under a dark sky, and the reason
 * narrowband from a dark site wants very long subs.
 */
export function optimalSubSeconds(p: Omit<ExposureParams, "subSeconds">, factor = 10): number {
  const background = p.skyRate + p.darkRate;
  if (background <= 0) return Infinity;
  return (factor * p.readNoise * p.readNoise) / background;
}

/** Longest sub before the brightest pixel of interest saturates. */
export function saturationLimitSeconds(
  p: Omit<ExposureParams, "subSeconds">,
  camera: Camera,
  headroom = 0.8,
): number {
  const rate = p.objectRate + p.skyRate + p.darkRate;
  if (rate <= 0) return Infinity;
  return (camera.fullWellE * headroom) / rate;
}

export type BudgetChannel = {
  id: string;
  params: ExposureParams;
  /** Relative importance. Equal weights equalise SNR across channels. */
  weight?: number;
};

export type BudgetAllocation = {
  id: string;
  hours: number;
  snr: number;
};

/**
 * Split a fixed integration budget across filters.
 *
 * Equalising weighted SNR is also the allocation that maximises the weakest
 * channel, so one formula serves both objectives. Writing SNR_i = sqrt(T_i).k_i
 * with k_i = e_obj / sqrt(variance), holding w_i.SNR_i equal gives
 * T_i proportional to 1/(w_i.k_i)^2.
 *
 * In SHO the result is nearly always dominated by OIII, and the UI should say
 * so rather than quietly handing over a lopsided split.
 */
export function allocateBudget(
  channels: BudgetChannel[],
  totalHours: number,
): BudgetAllocation[] {
  const usable = channels.filter((c) => c.params.objectRate > 0);
  if (usable.length === 0 || totalHours <= 0) {
    return channels.map((c) => ({ id: c.id, hours: 0, snr: 0 }));
  }

  const totalSeconds = totalHours * 3600;

  // k_i: SNR per sqrt(second) for this channel.
  const terms = usable.map((c) => {
    const variance = noisePerSecond(c.params);
    const k = c.params.objectRate / Math.sqrt(variance);
    const w = c.weight ?? 1;
    return { id: c.id, params: c.params, inv: 1 / (w * w * k * k) };
  });

  const sumInv = terms.reduce((acc, t) => acc + t.inv, 0);

  const byId = new Map<string, BudgetAllocation>();
  for (const t of terms) {
    const seconds = totalSeconds * (t.inv / sumInv);
    byId.set(t.id, {
      id: t.id,
      hours: seconds / 3600,
      snr: snrAt(t.params, seconds),
    });
  }

  return channels.map(
    (c) => byId.get(c.id) ?? { id: c.id, hours: 0, snr: 0 },
  );
}

/** Sampled SNR-vs-hours series for plotting. */
export function snrCurve(
  p: ExposureParams,
  maxHours: number,
  samples = 120,
): { hours: number; snr: number }[] {
  const out: { hours: number; snr: number }[] = [];
  for (let i = 0; i <= samples; i++) {
    const hours = (maxHours * i) / samples;
    out.push({ hours, snr: snrAt(p, hours * 3600) });
  }
  return out;
}
