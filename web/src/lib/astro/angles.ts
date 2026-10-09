// angularSeparation VENDORED from D:\Python\AstroPlanner\src\lib\wcs.ts (2026-10-09), unmodified.

const DEG = Math.PI / 180;

/** Angular separation between two sky positions, in degrees. */
export function angularSeparation(
  ra1: number,
  dec1: number,
  ra2: number,
  dec2: number,
): number {
  const d1 = dec1 * DEG;
  const d2 = dec2 * DEG;
  const dA = (ra2 - ra1) * DEG;
  const cosSep =
    Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(dA);
  return Math.acos(Math.min(1, Math.max(-1, cosSep))) / DEG;
}
