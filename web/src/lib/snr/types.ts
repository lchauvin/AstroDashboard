// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
import type { EmissionLine } from "./constants";

export type QePoint = { nm: number; qe: number };

export type Telescope = {
  id: string;
  name: string;
  apertureMm: number;
  /** Central obstruction diameter, mm. 0 for a refractor. */
  obstructionMm: number;
  focalLengthMm: number;
  /** Optical throughput excluding the filter: coatings, correctors, mirrors. */
  transmission: number;
};

export type Camera = {
  id: string;
  name: string;
  sensor: string;
  pixelSizeUm: number;
  widthPx: number;
  heightPx: number;
  /** Read noise in e- RMS at the operating gain. */
  readNoiseE: number;
  /** Dark current in e-/s/px at the operating temperature. */
  darkCurrentEPerSec: number;
  fullWellE: number;
  gainEPerAdu: number;
  /** Quantum efficiency curve; interpolated linearly between points. */
  qe: QePoint[];
};

export type FilterSpec = {
  id: string;
  name: string;
  centerNm: number;
  /** Full width at half maximum, nm. The bandwidth the sky is integrated over. */
  fwhmNm: number;
  peakTransmission: number;
  /** Which line this filter isolates, if any. Absent for broadband. */
  line?: EmissionLine;
  /**
   * Sky spectral factor. The sky model assumes a flat spectrum, but light
   * pollution is line-rich (Na 589, Hg 546) and airglow varies nightly, so the
   * true in-band sky can differ from flat by a factor of a few. 1.0 = flat;
   * calibration against your own subs solves for the real value.
   */
  skyLineFactor: number;
};

export type Rig = {
  id: string;
  name: string;
  telescope: Telescope;
  camera: Camera;
  filters: FilterSpec[];
};

/** Everything the SNR equation needs for one filter on one target. */
export type ExposureParams = {
  /** Target signal, e-/s/px. */
  objectRate: number;
  /** Sky background, e-/s/px. */
  skyRate: number;
  /** Dark current, e-/s/px. */
  darkRate: number;
  /** Read noise, e- RMS per sub. */
  readNoise: number;
  /** Sub-exposure length, seconds. */
  subSeconds: number;
};
