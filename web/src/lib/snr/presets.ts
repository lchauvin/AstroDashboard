// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * Equipment presets.
 *
 * Vendor-published figures, seeded so you pick from a list instead of typing
 * numbers. Every field is overridable — read noise in particular depends on the
 * gain you actually run, and QE curves are approximations digitised from
 * published plots, not measurements of your specific copy.
 */

import type { Camera, Telescope } from "./types";

export const TELESCOPE_PRESETS: Telescope[] = [
  {
    id: "redcat51",
    name: "William Optics RedCat 51",
    apertureMm: 51,
    obstructionMm: 0,
    focalLengthMm: 250,
    transmission: 0.95,
  },
  {
    id: "redcat61",
    name: "William Optics RedCat 61",
    apertureMm: 61,
    obstructionMm: 0,
    focalLengthMm: 300,
    transmission: 0.95,
  },
  {
    id: "esprit100",
    name: "Sky-Watcher Esprit 100ED",
    apertureMm: 100,
    obstructionMm: 0,
    focalLengthMm: 550,
    transmission: 0.93,
  },
  {
    id: "edgehd8",
    name: "Celestron EdgeHD 8",
    apertureMm: 203,
    obstructionMm: 68,
    focalLengthMm: 2032,
    transmission: 0.85,
  },
  {
    id: "newt200",
    name: "200mm f/4 Newtonian",
    apertureMm: 200,
    obstructionMm: 70,
    focalLengthMm: 800,
    transmission: 0.82,
  },
];

/**
 * QE curves as {nm, qe} points, linearly interpolated between.
 * Digitised from vendor plots; treat as +/- a few percent.
 */
const IMX585_QE = [
  { nm: 400, qe: 0.55 },
  { nm: 450, qe: 0.8 },
  { nm: 500, qe: 0.91 },
  { nm: 550, qe: 0.9 },
  { nm: 600, qe: 0.85 },
  { nm: 656, qe: 0.8 },
  { nm: 700, qe: 0.72 },
  { nm: 800, qe: 0.5 },
];

const IMX571_QE = [
  { nm: 400, qe: 0.5 },
  { nm: 450, qe: 0.78 },
  { nm: 500, qe: 0.9 },
  { nm: 550, qe: 0.88 },
  { nm: 600, qe: 0.8 },
  { nm: 656, qe: 0.72 },
  { nm: 700, qe: 0.62 },
  { nm: 800, qe: 0.35 },
];

const IMX533_QE = [
  { nm: 400, qe: 0.5 },
  { nm: 450, qe: 0.78 },
  { nm: 500, qe: 0.91 },
  { nm: 550, qe: 0.88 },
  { nm: 600, qe: 0.8 },
  { nm: 656, qe: 0.7 },
  { nm: 700, qe: 0.6 },
  { nm: 800, qe: 0.33 },
];

const IMX455_QE = [
  { nm: 400, qe: 0.52 },
  { nm: 450, qe: 0.8 },
  { nm: 500, qe: 0.91 },
  { nm: 550, qe: 0.89 },
  { nm: 600, qe: 0.82 },
  { nm: 656, qe: 0.74 },
  { nm: 700, qe: 0.64 },
  { nm: 800, qe: 0.36 },
];

export const CAMERA_PRESETS: Camera[] = [
  {
    id: "minicam8m",
    name: "QHY Minicam8M",
    sensor: "IMX585",
    pixelSizeUm: 2.9,
    widthPx: 3856,
    heightPx: 2180,
    readNoiseE: 1.2,
    darkCurrentEPerSec: 0.003,
    fullWellE: 39000,
    gainEPerAdu: 0.35,
    qe: IMX585_QE,
  },
  {
    id: "asi585mc",
    name: "ZWO ASI585MM Pro",
    sensor: "IMX585",
    pixelSizeUm: 2.9,
    widthPx: 3856,
    heightPx: 2180,
    readNoiseE: 1.2,
    darkCurrentEPerSec: 0.003,
    fullWellE: 39000,
    gainEPerAdu: 0.35,
    qe: IMX585_QE,
  },
  {
    id: "asi2600mm",
    name: "ZWO ASI2600MM Pro",
    sensor: "IMX571",
    pixelSizeUm: 3.76,
    widthPx: 6248,
    heightPx: 4176,
    readNoiseE: 1.5,
    darkCurrentEPerSec: 0.002,
    fullWellE: 50000,
    gainEPerAdu: 0.25,
    qe: IMX571_QE,
  },
  {
    id: "asi533mm",
    name: "ZWO ASI533MM Pro",
    sensor: "IMX533",
    pixelSizeUm: 3.76,
    widthPx: 3008,
    heightPx: 3008,
    readNoiseE: 1.5,
    darkCurrentEPerSec: 0.002,
    fullWellE: 50000,
    gainEPerAdu: 0.25,
    qe: IMX533_QE,
  },
  {
    id: "asi6200mm",
    name: "ZWO ASI6200MM Pro",
    sensor: "IMX455",
    pixelSizeUm: 3.76,
    widthPx: 9576,
    heightPx: 6388,
    readNoiseE: 1.5,
    darkCurrentEPerSec: 0.002,
    fullWellE: 51000,
    gainEPerAdu: 0.25,
    qe: IMX455_QE,
  },
];

export const telescopePreset = (id: string) => TELESCOPE_PRESETS.find((t) => t.id === id);
export const cameraPreset = (id: string) => CAMERA_PRESETS.find((c) => c.id === id);
