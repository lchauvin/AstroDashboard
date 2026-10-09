/**
 * Per-filter sky background for planning: the site's dark sky plus moonlight.
 *
 * Built on the vendored AstroPlanner model (Krisciunas & Schaefer 1991 for the
 * moon, flat-spectrum V-band conversion for the electron rate), with two
 * dashboard additions, both stated plainly:
 *
 * 1. The calibration factor kappa (AstroPlanner's `skyLineFactor`) applies to
 *    the DARK sky only. It exists because light pollution is line-rich (Na, Hg)
 *    and airglow varies; moonlight is scattered sunlight, a continuum, so it
 *    is not scaled by it.
 *
 * 2. Moonlight colour. KS91 is a V-band model; its Rayleigh term is scattered
 *    as lambda^-4, so the moonlit sky is bluer than V. The Rayleigh part is
 *    scaled by (550 / lambda)^4 at the filter's centre; the aerosol (Mie) part,
 *    roughly grey, is left as is. This is why OIII and B lose more to the moon
 *    than Hα and SII — the effect every imager knows, from the model rather than
 *    from a rule of thumb.
 */

import { airmass, apertureAreaCm2, extinctionFactor, throughput, VEGA_PHOTON_FLUX, type FilterSpec, type MoonGeometry } from "@/lib/snr";
import type { PlanRig } from "./rig";

/** KS91 moonlight split into its Rayleigh and Mie parts, nanoLamberts (V). Sum = vendored moonSkyBrightnessNl. */
export function moonlightNl(moon: MoonGeometry, extinctionK = 0.2): { rayleigh: number; mie: number } {
  if (moon.altitudeDeg <= 0 || moon.targetAltitudeDeg <= 0) return { rayleigh: 0, mie: 0 };
  const illum = Math.min(1, Math.max(0, moon.illumination));
  const alpha = (Math.acos(2 * illum - 1) * 180) / Math.PI;
  const iStar = Math.pow(10, -0.4 * (3.84 + 0.026 * Math.abs(alpha) + 4e-9 * Math.pow(alpha, 4)));
  const cosRho = Math.cos((moon.separationDeg * Math.PI) / 180);
  const coupling =
    iStar *
    Math.pow(10, -0.4 * extinctionK * airmass(moon.altitudeDeg)) *
    (1 - Math.pow(10, -0.4 * extinctionK * airmass(moon.targetAltitudeDeg)));
  return {
    rayleigh: Math.pow(10, 5.36) * (1.06 + cosRho * cosRho) * coupling,
    mie: Math.pow(10, 6.15 - moon.separationDeg / 40) * coupling,
  };
}

/** e⁻/s/px per unit of V flux 10^(-0.4 m) for a flat spectrum through this filter (κ = 1). */
export function fluxToRate(rig: PlanRig, filter: FilterSpec): number {
  return (
    VEGA_PHOTON_FLUX *
    filter.fwhmNm *
    10 *
    apertureAreaCm2(rig.telescope) *
    rig.pixelScale *
    rig.pixelScale *
    throughput(rig.telescope, rig.camera, filter)
  );
}

/** nanoLamberts → linear V flux in units of 10^(-0.4 m) per arcsec² (inverse of KS91's mag conversion). */
export const nlToFlux = (nl: number) => Math.pow(10, -0.4 * ((20.7233 - Math.log(nl / 34.08)) / 0.92104));

export const moonColour = (filter: FilterSpec) => Math.pow(550 / filter.centerNm, 4);

export type FilterSky = {
  filter: FilterSpec;
  /** Dark-sky (no moon) electron rate, κ applied. */
  darkRate: number;
  /** Rate per nanoLambert of Rayleigh / Mie moonlight. */
  perNlRayleigh: number;
  perNlMie: number;
  kappa: number;
};

/** Precomputed per-filter sky terms for a site. */
export function filterSky(rig: PlanRig, filter: FilterSpec, siteSqm: number, kappa = 1): FilterSky {
  const k = fluxToRate(rig, filter);
  const perNl = k * nlToFlux(1);
  return {
    filter,
    darkRate: kappa * k * Math.pow(10, -0.4 * siteSqm),
    perNlRayleigh: perNl * moonColour(filter),
    perNlMie: perNl,
    kappa,
  };
}

export function skyRate(fs: FilterSky, moon: { rayleigh: number; mie: number } | null): number {
  if (!moon) return fs.darkRate;
  return fs.darkRate + moon.rayleigh * fs.perNlRayleigh + moon.mie * fs.perNlMie;
}

/** Target signal at an altitude, from its zenith rate (extinction by filter). */
export const atAltitude = (zenithRate: number, filter: FilterSpec, altitudeDeg: number) =>
  (zenithRate * extinctionFactor(filter, airmass(altitudeDeg))) / extinctionFactor(filter, 1);
