// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * Filter presets.
 *
 * `fwhmNm` is what the sky model integrates over, so it is the number that
 * decides how much light pollution a filter lets through — the single most
 * important parameter here for anyone imaging from a bright site.
 */

import type { FilterSpec } from "./types";
import { LINE_WAVELENGTH_NM } from "./constants";

const nb = (
  id: string,
  name: string,
  line: keyof typeof LINE_WAVELENGTH_NM,
  fwhmNm: number,
  peakTransmission = 0.9,
): FilterSpec => ({
  id,
  name,
  centerNm: LINE_WAVELENGTH_NM[line],
  fwhmNm,
  peakTransmission,
  line,
  skyLineFactor: 1,
});

export const NARROWBAND_PRESETS: FilterSpec[] = [
  nb("ha-3", "H-alpha 3nm", "Ha", 3, 0.9),
  nb("ha-35", "H-alpha 3.5nm", "Ha", 3.5, 0.9),
  nb("ha-45", "H-alpha 4.5nm", "Ha", 4.5, 0.92),
  nb("ha-65", "H-alpha 6.5nm", "Ha", 6.5, 0.95),
  nb("ha-12", "H-alpha 12nm", "Ha", 12, 0.95),
  nb("oiii-3", "OIII 3nm", "OIII", 3, 0.9),
  nb("oiii-35", "OIII 3.5nm", "OIII", 3.5, 0.9),
  nb("oiii-45", "OIII 4.5nm", "OIII", 4.5, 0.92),
  nb("oiii-65", "OIII 6.5nm", "OIII", 6.5, 0.95),
  nb("sii-3", "SII 3nm", "SII", 3, 0.9),
  nb("sii-35", "SII 3.5nm", "SII", 3.5, 0.9),
  nb("sii-45", "SII 4.5nm", "SII", 4.5, 0.92),
  nb("sii-65", "SII 6.5nm", "SII", 6.5, 0.95),
];

export const BROADBAND_PRESETS: FilterSpec[] = [
  { id: "lum", name: "Luminance", centerNm: 550, fwhmNm: 300, peakTransmission: 0.97, skyLineFactor: 1 },
  { id: "red", name: "Red", centerNm: 620, fwhmNm: 90, peakTransmission: 0.97, skyLineFactor: 1 },
  { id: "green", name: "Green", centerNm: 535, fwhmNm: 90, peakTransmission: 0.97, skyLineFactor: 1 },
  { id: "blue", name: "Blue", centerNm: 450, fwhmNm: 90, peakTransmission: 0.97, skyLineFactor: 1 },
];

export const ALL_FILTER_PRESETS = [...NARROWBAND_PRESETS, ...BROADBAND_PRESETS];

export function filterPreset(id: string): FilterSpec | undefined {
  return ALL_FILTER_PRESETS.find((f) => f.id === id);
}

/** The SHO set at a given bandwidth — the common starting point. */
export function shoSet(fwhmNm: 3 | 3.5 | 4.5 | 6.5 = 3): FilterSpec[] {
  const key = { 3: "3", 3.5: "35", 4.5: "45", 6.5: "65" }[fwhmNm];
  return ["sii", "ha", "oiii"]
    .map((line) => filterPreset(`${line}-${key}`))
    .filter((f): f is FilterSpec => Boolean(f));
}
