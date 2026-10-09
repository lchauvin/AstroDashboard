// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * The sky background — the term that decides everything from Bortle 9.
 *
 * Two parts: a static darkness for the site (an SQM reading in mag/arcsec^2),
 * and a moonlight contribution computed from geometry. They are added in linear
 * units, then converted back to a magnitude and turned into an electron rate.
 *
 * KNOWN LIMITATION, stated plainly because it is the model's weakest link:
 * the in-band sky flux is derived from a V-band magnitude assuming a *flat*
 * spectrum. Real light pollution is line-rich (sodium 589 nm, mercury 546 nm)
 * and airglow varies from night to night, so a narrowband filter can see
 * meaningfully more or less sky than flat-spectrum scaling predicts. Every
 * filter therefore carries a `skyLineFactor` (kappa, default 1.0) which the
 * calibration step solves for from your own subs. Until it is calibrated, treat
 * narrowband sky rates as an estimate good to a factor of ~2, not a measurement.
 */

import { BORTLE_BANDS, NATURAL_SKY_MAG, VEGA_PHOTON_FLUX } from "./constants";
import { airmass, apertureAreaCm2, throughput, type SignalContext } from "./photometry";

/** Bortle class for an SQM reading. Display only — SQM is the stored value. */
export function bortleFromSqm(sqm: number): { class: number; label: string } {
  const band = BORTLE_BANDS.find((b) => sqm >= b.minSqm) ?? BORTLE_BANDS[BORTLE_BANDS.length - 1];
  return { class: band.class, label: band.label };
}

/** A representative SQM for a Bortle class — for seeding a slider, not for physics. */
export function sqmFromBortle(bortleClass: number): number {
  const i = BORTLE_BANDS.findIndex((b) => b.class === bortleClass);
  if (i < 0) return 20.0;
  const lower = BORTLE_BANDS[i].minSqm;
  const upper = i === 0 ? NATURAL_SKY_MAG : BORTLE_BANDS[i - 1].minSqm;
  if (!Number.isFinite(lower)) return 17.2; // Bortle 9 has no lower bound
  return (lower + upper) / 2;
}

/** Surface brightness in nanoLamberts, from mag/arcsec^2. (Garstang / KS91.) */
export function magToNanoLambert(mag: number): number {
  return 34.08 * Math.exp(20.7233 - 0.92104 * mag);
}

/** The inverse. */
export function nanoLambertToMag(nl: number): number {
  return (20.7233 - Math.log(nl / 34.08)) / 0.92104;
}

export type MoonGeometry = {
  /** Illuminated fraction, 0 (new) to 1 (full). */
  illumination: number;
  /** Moon altitude in degrees; below 0 contributes nothing. */
  altitudeDeg: number;
  /** Angular separation between moon and target, degrees. */
  separationDeg: number;
  /** Target altitude in degrees. */
  targetAltitudeDeg: number;
};

/**
 * Moonlight sky brightness in nanoLamberts.
 *
 * Krisciunas & Schaefer (1991), "A model of the brightness of moonlight",
 * PASP 103, 1033. The three pieces are the moon's illuminance outside the
 * atmosphere, a scattering function combining Rayleigh and Mie terms, and the
 * airmass-dependent coupling between the moon's light path and the target's.
 */
export function moonSkyBrightnessNl(moon: MoonGeometry, extinctionK = 0.2): number {
  if (moon.altitudeDeg <= 0) return 0;
  if (moon.targetAltitudeDeg <= 0) return 0;

  // Phase angle: 0 deg at full moon, 180 at new.
  const clampedIllum = Math.min(1, Math.max(0, moon.illumination));
  const alpha = (Math.acos(2 * clampedIllum - 1) * 180) / Math.PI;

  // Illuminance of the moon outside the atmosphere (KS91 eq. 20).
  const iStar = Math.pow(10, -0.4 * (3.84 + 0.026 * Math.abs(alpha) + 4e-9 * Math.pow(alpha, 4)));

  // Scattering function (KS91 eq. 21): Rayleigh term + aerosol/Mie term.
  const rho = (moon.separationDeg * Math.PI) / 180;
  const cosRho = Math.cos(rho);
  const fRho =
    Math.pow(10, 5.36) * (1.06 + cosRho * cosRho) +
    Math.pow(10, 6.15 - moon.separationDeg / 40);

  const xMoon = airmass(moon.altitudeDeg);
  const xTarget = airmass(moon.targetAltitudeDeg);

  // KS91 eq. 15.
  return (
    fRho *
    iStar *
    Math.pow(10, -0.4 * extinctionK * xMoon) *
    (1 - Math.pow(10, -0.4 * extinctionK * xTarget))
  );
}

/**
 * Effective sky brightness in mag/arcsec^2 once the moon is included.
 * Brightness adds linearly; magnitudes do not.
 */
export function effectiveSkyMag(
  siteSqm: number,
  moon: MoonGeometry | null,
  extinctionK = 0.2,
): number {
  const darkNl = magToNanoLambert(siteSqm);
  const moonNl = moon ? moonSkyBrightnessNl(moon, extinctionK) : 0;
  return nanoLambertToMag(darkNl + moonNl);
}

/**
 * Sky electron rate per pixel, e-/s/px.
 *
 * No extinction term: sky glow is produced and scattered within the atmosphere
 * itself, so it is not attenuated by it the way target light is.
 *
 * `skyMag` is a zenith value, as an SQM meter reports. A target low in the sky
 * genuinely sees more sky than this predicts; `skyAirmassFactor` exists so that
 * can be applied explicitly rather than being silently invented here.
 */
export function skyElectronRate(
  ctx: SignalContext,
  skyMag: number,
  skyAirmassFactor = 1,
): number {
  const fluxPerAngstrom = VEGA_PHOTON_FLUX * Math.pow(10, -0.4 * skyMag);
  const bandwidthAngstrom = ctx.filter.fwhmNm * 10;

  return (
    fluxPerAngstrom *
    bandwidthAngstrom *
    ctx.filter.skyLineFactor *
    skyAirmassFactor *
    apertureAreaCm2(ctx.telescope) *
    ctx.pixelScale *
    ctx.pixelScale *
    throughput(ctx.telescope, ctx.camera, ctx.filter)
  );
}

/**
 * Back-solve the site's effective sky magnitude from a measured sky rate.
 *
 * This is the calibration direction: you measure the background of a real
 * calibrated sub in e-/s/px, and this returns the mag/arcsec^2 the model would
 * need to reproduce it. Comparing that to your nominal SQM gives `skyLineFactor`.
 */
export function skyMagFromRate(ctx: SignalContext, rate: number): number {
  const bandwidthAngstrom = ctx.filter.fwhmNm * 10;
  const perMagUnit =
    VEGA_PHOTON_FLUX *
    bandwidthAngstrom *
    ctx.filter.skyLineFactor *
    apertureAreaCm2(ctx.telescope) *
    ctx.pixelScale *
    ctx.pixelScale *
    throughput(ctx.telescope, ctx.camera, ctx.filter);
  if (rate <= 0 || perMagUnit <= 0) return Infinity;
  return -2.5 * Math.log10(rate / perMagUnit);
}
