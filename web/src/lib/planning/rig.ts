/**
 * The rig the SNR model uses: presets matched from the FITS headers, then the
 * user's overrides from Settings → Rig.
 */

import {
  BROADBAND_PRESETS,
  CAMERA_PRESETS,
  NARROWBAND_PRESETS,
  TELESCOPE_PRESETS,
  filterPreset,
  type Camera,
  type FilterSpec,
  type Telescope,
} from "@/lib/snr";
import { pixelScaleArcsec } from "@/lib/astro/coordinates";
import type { DetectedRig, RigSettings } from "./types";

export const FILTER_LETTERS = ["L", "R", "G", "B", "H", "O", "S"] as const;
export type FilterLetter = (typeof FILTER_LETTERS)[number];

export const FILTER_NAMES: Record<string, string> = {
  L: "Luminance", R: "Red", G: "Green", B: "Blue", H: "Hα", O: "OIII", S: "SII",
};

/** Defaults when nothing is saved: 3 nm narrowband (the common SHO set), standard LRGB. */
export const DEFAULT_FILTER_PRESETS: Record<FilterLetter, string> = {
  L: "lum", R: "red", G: "green", B: "blue", H: "ha-3", O: "oiii-3", S: "sii-3",
};

export const FILTER_PRESET_CHOICES: Record<FilterLetter, FilterSpec[]> = {
  L: BROADBAND_PRESETS.filter((f) => f.id === "lum"),
  R: BROADBAND_PRESETS.filter((f) => f.id === "red"),
  G: BROADBAND_PRESETS.filter((f) => f.id === "green"),
  B: BROADBAND_PRESETS.filter((f) => f.id === "blue"),
  H: NARROWBAND_PRESETS.filter((f) => f.line === "Ha"),
  O: NARROWBAND_PRESETS.filter((f) => f.line === "OIII"),
  S: NARROWBAND_PRESETS.filter((f) => f.line === "SII"),
};

export type PlanRig = {
  telescope: Telescope;
  camera: Camera;
  pixelScale: number;
  /** Field of view in degrees. */
  fov: { width: number; height: number };
  filters: Partial<Record<FilterLetter, FilterSpec>>;
  /** Sub length per filter, seconds (what you actually shoot, from the headers). */
  subSeconds: Partial<Record<FilterLetter, number>>;
  /** Filters you own (shot in the last year, or configured). */
  owned: FilterLetter[];
  source: { telescope: "preset" | "headers"; camera: "preset" | "headers" };
};

const squash = (s: string | null | undefined) => (s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");

function matchTelescope(name: string | null, focalLength: number | null): Telescope | undefined {
  const key = squash(name);
  if (!key) return undefined;
  return TELESCOPE_PRESETS.find((t) => {
    const preset = squash(t.name);
    return preset.includes(key) || key.includes(squash(t.id)) || (focalLength != null && preset.endsWith(key) && t.focalLengthMm === focalLength);
  });
}

function matchCamera(name: string | null): Camera | undefined {
  const key = squash(name);
  if (!key) return undefined;
  return CAMERA_PRESETS.find((c) => key.includes(squash(c.id)) || squash(c.name).includes(key) || key.includes(squash(c.name)));
}

export function buildRig(detected: DetectedRig | null, saved: RigSettings | null): PlanRig {
  const s = saved ?? {};
  const presetTel =
    (s.telescopeId && TELESCOPE_PRESETS.find((t) => t.id === s.telescopeId)) ||
    matchTelescope(detected?.telescop ?? null, detected?.focalLength ?? null);
  const telescope: Telescope = presetTel
    ? { ...presetTel }
    : {
        id: "headers",
        name: detected?.telescop ?? "Telescope from headers",
        // Unknown aperture: assume f/5, the typical small refractor, until set in Settings.
        apertureMm: (detected?.focalLength ?? 250) / 5,
        obstructionMm: 0,
        focalLengthMm: detected?.focalLength ?? 250,
        transmission: 0.93,
      };
  if (s.apertureMm) telescope.apertureMm = s.apertureMm;
  if (s.focalLengthMm) telescope.focalLengthMm = s.focalLengthMm;
  else if (detected?.focalLength) telescope.focalLengthMm = detected.focalLength;

  const presetCam = (s.cameraId && CAMERA_PRESETS.find((c) => c.id === s.cameraId)) || matchCamera(detected?.instrume ?? null);
  const camera: Camera = presetCam
    ? { ...presetCam }
    : {
        ...CAMERA_PRESETS[0],
        id: "headers",
        name: detected?.instrume ?? "Camera from headers",
        pixelSizeUm: detected?.pixelSizeUm ?? CAMERA_PRESETS[0].pixelSizeUm,
        widthPx: detected?.widthPx ?? CAMERA_PRESETS[0].widthPx,
        heightPx: detected?.heightPx ?? CAMERA_PRESETS[0].heightPx,
      };
  if (s.readNoiseE) camera.readNoiseE = s.readNoiseE;

  const pixelScale = pixelScaleArcsec(camera.pixelSizeUm, telescope.focalLengthMm);
  const owned = (detected?.filters?.length ? detected.filters : ["H", "O", "S"]).filter((f): f is FilterLetter =>
    (FILTER_LETTERS as readonly string[]).includes(f),
  );
  const filters: PlanRig["filters"] = {};
  const subSeconds: PlanRig["subSeconds"] = {};
  for (const letter of FILTER_LETTERS) {
    const id = s.filterPresets?.[letter] ?? DEFAULT_FILTER_PRESETS[letter];
    const spec = filterPreset(id);
    if (spec) filters[letter] = { ...spec };
    subSeconds[letter] = s.subSeconds?.[letter] ?? detected?.subSeconds?.[letter] ?? (spec?.line ? 300 : 60);
  }
  return {
    telescope,
    camera,
    pixelScale,
    fov: { width: (camera.widthPx * pixelScale) / 3600, height: (camera.heightPx * pixelScale) / 3600 },
    filters,
    subSeconds,
    owned,
    source: { telescope: presetTel ? "preset" : "headers", camera: presetCam ? "preset" : "headers" },
  };
}
