// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * Photons in, electrons out.
 *
 * Every signal in AstroPlanner passes through here: a target's surface
 * brightness (in Rayleighs for an emission line, or mag/arcsec^2 for anything
 * broadband) becomes an electron rate per pixel for a specific rig.
 */

import {
  EXTINCTION_MAG_PER_AIRMASS,
  RAYLEIGH_TO_PHOTONS,
  VEGA_PHOTON_FLUX,
  type EmissionLine,
} from "./constants";
import type { Camera, FilterSpec, QePoint, Telescope } from "./types";

/** Collecting area in cm^2, net of the central obstruction. */
export function apertureAreaCm2(telescope: Telescope): number {
  const d = telescope.apertureMm / 10;
  const obs = telescope.obstructionMm / 10;
  return (Math.PI / 4) * (d * d - obs * obs);
}

/** Linear interpolation into a QE curve; clamps outside the tabulated range. */
export function qeAt(curve: QePoint[], nm: number): number {
  if (curve.length === 0) return 0;
  const points = [...curve].sort((a, b) => a.nm - b.nm);
  if (nm <= points[0].nm) return points[0].qe;
  if (nm >= points[points.length - 1].nm) return points[points.length - 1].qe;

  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (nm <= b.nm) {
      const t = (nm - a.nm) / (b.nm - a.nm);
      return a.qe + t * (b.qe - a.qe);
    }
  }
  return points[points.length - 1].qe;
}

/**
 * Airmass from altitude.
 *
 * Krisciunas & Schaefer (1991) eq. 3: X = (1 - 0.96 sin^2 Z)^-0.5, which stays
 * finite at the horizon where the plane-parallel sec(Z) diverges.
 */
export function airmass(altitudeDeg: number): number {
  if (altitudeDeg <= 0) return 40; // effectively "not observable"
  const z = (90 - altitudeDeg) * (Math.PI / 180);
  const sinZ = Math.sin(z);
  return Math.pow(1 - 0.96 * sinZ * sinZ, -0.5);
}

/** Extinction throughput (0-1) for a filter at a given airmass. */
export function extinctionFactor(filter: FilterSpec, airmassValue: number): number {
  const k = filter.line
    ? EXTINCTION_MAG_PER_AIRMASS[filter.line]
    : EXTINCTION_MAG_PER_AIRMASS.broadband;
  return Math.pow(10, -0.4 * k * airmassValue);
}

/** Everything between the sky and the sensor, excluding collecting area. */
export function throughput(
  telescope: Telescope,
  camera: Camera,
  filter: FilterSpec,
): number {
  return qeAt(camera.qe, filter.centerNm) * filter.peakTransmission * telescope.transmission;
}

export type SignalContext = {
  telescope: Telescope;
  camera: Camera;
  filter: FilterSpec;
  /** Pixel scale, arcsec/px. */
  pixelScale: number;
  /** Target altitude in degrees; drives extinction. Use 90 for "at zenith". */
  altitudeDeg: number;
};

/**
 * Electron rate per pixel from an emission line of surface brightness `rayleighs`.
 *
 * The whole chain, with nothing hidden:
 *   e-/s/px = S_R . 1.87041e-6 . A_tel . p^2 . QE . T_filter . T_optics . 10^(-0.4 k X)
 */
export function lineSignalRate(ctx: SignalContext, rayleighs: number): number {
  const x = airmass(ctx.altitudeDeg);
  return (
    rayleighs *
    RAYLEIGH_TO_PHOTONS *
    apertureAreaCm2(ctx.telescope) *
    ctx.pixelScale *
    ctx.pixelScale *
    throughput(ctx.telescope, ctx.camera, ctx.filter) *
    extinctionFactor(ctx.filter, x)
  );
}

/**
 * Electron rate per pixel from a broadband surface brightness in mag/arcsec^2.
 *
 * Same chain, but the flux comes from the V zero point integrated over the
 * filter's bandwidth, so it inherits the flat-spectrum assumption discussed in
 * sky.ts. Fine for luminance and RGB; meaningless for a narrowband filter,
 * which is what `lineSignalRate` is for.
 */
export function broadbandSignalRate(
  ctx: SignalContext,
  magPerArcsec2: number,
): number {
  const x = airmass(ctx.altitudeDeg);
  const fluxPerAngstrom = VEGA_PHOTON_FLUX * Math.pow(10, -0.4 * magPerArcsec2);
  const bandwidthAngstrom = ctx.filter.fwhmNm * 10;

  return (
    fluxPerAngstrom *
    bandwidthAngstrom *
    apertureAreaCm2(ctx.telescope) *
    ctx.pixelScale *
    ctx.pixelScale *
    throughput(ctx.telescope, ctx.camera, ctx.filter) *
    extinctionFactor(ctx.filter, x)
  );
}

/**
 * Mean surface brightness of an object of total magnitude `mag` spread over an
 * ellipse of `majAx` x `minAx` arcminutes.
 *
 * Only meaningful for broadband work, and only as an average — a galaxy's core
 * is orders of magnitude brighter than this and its outer disc far fainter.
 */
export function surfaceBrightnessFromMagnitude(
  mag: number,
  majAxArcmin: number,
  minAxArcmin: number,
): number | null {
  if (!majAxArcmin || !minAxArcmin) return null;
  const areaArcsec2 =
    (Math.PI / 4) * (majAxArcmin * 60) * (minAxArcmin * 60);
  if (areaArcsec2 <= 0) return null;
  return mag + 2.5 * Math.log10(areaArcsec2);
}

export type { EmissionLine };

/**
 * The inverse of `lineSignalRate`: what surface brightness would produce this
 * measured electron rate?
 *
 * This is the calibration direction, and it is how the H-alpha anchor stops
 * being an estimate. Measure the object's signal above background in one of
 * your own calibrated subs, and this returns the surface brightness in
 * Rayleighs that your rig actually saw — no survey beam, no class average, no
 * assumption about what a nebula of this type is usually like.
 */
export function rayleighsFromRate(ctx: SignalContext, rate: number): number {
  const perRayleigh = lineSignalRate(ctx, 1);
  return perRayleigh > 0 ? rate / perRayleigh : 0;
}

/**
 * Electron rate per pixel implied by a measured level in ADU.
 *
 * Expects a single calibrated sub, or an average-combined stack of them, which
 * share the same ADU scale. A SUM-combined stack does not, and would overstate
 * the rate by the number of frames.
 */
export function rateFromAdu(adu: number, gainEPerAdu: number, subSeconds: number): number {
  if (subSeconds <= 0) return 0;
  return (adu * gainEPerAdu) / subSeconds;
}
