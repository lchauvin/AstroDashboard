// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * Physical constants, each with the source it came from.
 *
 * Nothing here is tuned to make an answer come out nicely. Where a number is an
 * estimate rather than a measurement it says so, and there is a knob elsewhere
 * for calibrating it against real data (see sky.ts `skyLineFactor`).
 */

/**
 * Photon flux of a V = 0 source above the atmosphere.
 *
 * From the Bessell V zero point F_lambda(0) = 3.63e-9 erg cm^-2 s^-1 A^-1 at
 * 5500 A, divided by the photon energy hc/lambda = 3.61e-12 erg:
 *   3.63e-9 / 3.61e-12 = 1005 photons cm^-2 s^-1 A^-1
 */
export const VEGA_PHOTON_FLUX = 1005;

/**
 * One Rayleigh as a photon surface brightness.
 *
 *   1 R = 1e6 / (4*pi) photons cm^-2 s^-1 sr^-1   = 79577.47
 *   1 sr = 206264.806^2 arcsec^2                  = 4.254517e10
 *   => 1 R = 1.87041e-6 photons cm^-2 s^-1 arcsec^-2
 *
 * This is what makes the Finkbeiner H-alpha map (calibrated in Rayleighs)
 * directly usable as an absolute signal anchor rather than a relative image.
 */
export const RAYLEIGH_TO_PHOTONS = 1.87041e-6;

/** Arcseconds per radian; the 206.265 in the pixel-scale formula, times 1000. */
export const ARCSEC_PER_RAD = 206264.806;

/** Rest wavelengths of the narrowband lines, nm. */
export const LINE_WAVELENGTH_NM = {
  Ha: 656.28,
  OIII: 500.7,
  SII: 671.6, // 6716/6731 doublet, treated as one
} as const;

export type EmissionLine = keyof typeof LINE_WAVELENGTH_NM;

/**
 * Atmospheric extinction, magnitudes per airmass, at a good low-altitude site.
 * Wavelength-dependent because Rayleigh scattering goes as lambda^-4 — which is
 * why OIII suffers noticeably more than H-alpha near the horizon.
 * Typical sea-level values (Schaefer 1990; standard observatory practice).
 */
export const EXTINCTION_MAG_PER_AIRMASS = {
  Ha: 0.1,
  SII: 0.1,
  OIII: 0.23,
  broadband: 0.2, // V-band default
} as const;

/**
 * Natural sky brightness floor, mag/arcsec^2 in V.
 * The darkest achievable night sky: airglow plus zodiacal and integrated
 * starlight. Bortle 1 sits here, and no filter or travel goes below it.
 */
export const NATURAL_SKY_MAG = 22.0;

/**
 * Bortle class boundaries as SQM (mag/arcsec^2) upper bounds.
 * Bortle is a coarse label on a continuous quantity, so AstroPlanner stores SQM
 * and derives the class for display — never the other way round.
 */
export const BORTLE_BANDS: { readonly class: number; readonly minSqm: number; readonly label: string }[] = [
  { class: 1, minSqm: 21.7, label: "Excellent dark-sky site" },
  { class: 2, minSqm: 21.5, label: "Typical truly dark site" },
  { class: 3, minSqm: 21.3, label: "Rural sky" },
  { class: 4, minSqm: 20.4, label: "Rural/suburban transition" },
  { class: 5, minSqm: 19.1, label: "Suburban sky" },
  { class: 6, minSqm: 18.4, label: "Bright suburban sky" },
  { class: 7, minSqm: 18.0, label: "Suburban/urban transition" },
  { class: 8, minSqm: 17.5, label: "City sky" },
  { class: 9, minSqm: -Infinity, label: "Inner-city sky" },
];
