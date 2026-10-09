// VENDORED from D:\Python\AstroPlanner\src\lib\coordinates.ts (2026-10-09), unmodified.
/**
 * Parsing and formatting of human-written sky coordinates, so a target can be
 * entered as "RA 20h 12m · Dec +38° 21′" when it is not in the catalog.
 *
 * Deliberately forgiving about separators and units — this field is written for
 * humans, so a failed parse is normal and simply means the caller falls back to
 * a name lookup.
 *
 * PROVENANCE: adapted from D:\Python\AstroBlog\src\lib\coordinates.ts
 * (vendored 2026-09-07). AstroPlanner owns this copy; it is pinned by
 * scripts/verify-vendored.ts. Additions: formatRa/formatDec (AstroPlanner needs
 * to render coordinates, not only read them) and pixelScaleArcsec/fieldOfView.
 */
export type SkyPosition = { ra: number; dec: number };

const num = (s: string | undefined) => (s === undefined ? 0 : Number(s));

export function parseRaDec(input: string): SkyPosition | null {
  if (!input) return null;
  const text = input.replace(/\s+/g, " ");

  // RA: "02h 51m", "02h51m30s", "RA 2 51 30"
  const raMatch =
    /RA[^0-9+-]*(\d{1,2})\s*h\s*(\d{1,2}(?:\.\d+)?)\s*m?\s*(?:(\d{1,2}(?:\.\d+)?)\s*s)?/i.exec(
      text,
    );

  // Dec: "+60° 26′", "-16d 12' 30\"", "+60 26"
  const decMatch =
    /Dec[^0-9+-]*([+-]?\d{1,2})\s*(?:°|d|:)\s*(\d{1,2}(?:\.\d+)?)\s*(?:['′:]\s*(\d{1,2}(?:\.\d+)?)\s*["″]?)?/i.exec(
      text,
    );

  if (!raMatch || !decMatch) return null;

  const raHours = num(raMatch[1]) + num(raMatch[2]) / 60 + num(raMatch[3]) / 3600;
  const ra = raHours * 15; // hours -> degrees

  const decSign = decMatch[1].trim().startsWith("-") ? -1 : 1;
  const dec =
    decSign *
    (Math.abs(num(decMatch[1])) + num(decMatch[2]) / 60 + num(decMatch[3]) / 3600);

  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
  if (ra < 0 || ra > 360 || dec < -90 || dec > 90) return null;

  return { ra, dec };
}

/** Half-diagonal of the field in degrees. */
export function fieldRadiusDegrees(
  widthPx: number,
  heightPx: number,
  arcsecPerPx: number,
): number | null {
  if (!widthPx || !heightPx || !arcsecPerPx) return null;
  const wDeg = (widthPx * arcsecPerPx) / 3600;
  const hDeg = (heightPx * arcsecPerPx) / 3600;
  return Math.hypot(wDeg, hDeg) / 2;
}

/**
 * The one formula every rig calculation starts from.
 * 206.265 = arcsec per radian / 1000, folding the µm -> mm unit change in.
 *
 * Reference: astro_eval/image_loader.py:100 uses the identical expression.
 */
export function pixelScaleArcsec(pixelSizeUm: number, focalLengthMm: number): number {
  if (!pixelSizeUm || !focalLengthMm) return 0;
  return (206.265 * pixelSizeUm) / focalLengthMm;
}

/** Sensor field of view in degrees, given its pixel dimensions and scale. */
export function fieldOfViewDegrees(
  widthPx: number,
  heightPx: number,
  arcsecPerPx: number,
): { width: number; height: number } {
  return {
    width: (widthPx * arcsecPerPx) / 3600,
    height: (heightPx * arcsecPerPx) / 3600,
  };
}

/** 303.027 -> "20h 12m 06.5s" */
export function formatRa(raDeg: number): string {
  const hours = (((raDeg % 360) + 360) % 360) / 15;
  const h = Math.floor(hours);
  const minutes = (hours - h) * 60;
  const m = Math.floor(minutes);
  const s = (minutes - m) * 60;
  return `${h}h ${String(m).padStart(2, "0")}m ${s.toFixed(1).padStart(4, "0")}s`;
}

/** 38.3549 -> "+38° 21′ 18″" */
export function formatDec(decDeg: number): string {
  const sign = decDeg < 0 ? "−" : "+";
  const abs = Math.abs(decDeg);
  const d = Math.floor(abs);
  const arcmin = (abs - d) * 60;
  const m = Math.floor(arcmin);
  const s = Math.round((arcmin - m) * 60);
  return `${sign}${d}° ${String(m).padStart(2, "0")}′ ${String(s).padStart(2, "0")}″`;
}
