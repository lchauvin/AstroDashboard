/**
 * How bright a target is in each filter, as an electron rate per pixel at the zenith.
 *
 * Emission lines come from the Hα anchor (Finkbeiner survey when the object is
 * resolved, otherwise a per-class typical value — AstroPlanner's halpha.ts),
 * with OIII and SII derived through the class line ratios. Broadband filters
 * get the catalog's mean surface brightness plus any emission line that falls
 * inside their passband. Every number says where it came from.
 */

import {
  DEFAULT_LINE_RATIOS,
  LINE_WAVELENGTH_NM,
  OBJECT_CLASS_LABEL,
  broadbandSignalRate,
  emitsLines,
  lineSignalRate,
  surfaceBrightnessFromMagnitude,
  type ObjectClass,
  type SignalContext,
} from "@/lib/snr";
import type { FilterLetter, PlanRig } from "./rig";
import type { HalphaMeasurement } from "./types";

/**
 * Characteristic Hα surface brightness by class, Rayleighs.
 * VENDORED from AstroPlanner src/server/halpha.ts TYPICAL_HALPHA_R (2026-10-09): estimates of
 * the bright regions people frame, for targets the ~1° survey beam cannot resolve.
 */
export const TYPICAL_HALPHA_R: Record<ObjectClass, number> = {
  hii: 500,
  wolfrayet: 400,
  planetary: 5000,
  snr: 150,
  galaxy: 30,
  reflection: 0,
  dark: 0,
  cluster: 0,
  star: 0,
  unknown: 200,
};

export type CatalogInfo = {
  name: string;
  ra: number;
  dec: number;
  majAxArcmin: number | null;
  minAxArcmin: number | null;
  paDeg: number | null;
  type: string;
  vmag: number | null;
  surfBr: number | null;
  commonName: string | null;
  objectClass: ObjectClass;
};

export type HalphaAnchor = {
  rayleighs: number;
  basis: "survey" | "typical" | "none";
  note: string;
  survey: HalphaMeasurement | null;
};

export function halphaAnchor(cls: ObjectClass, survey: HalphaMeasurement | null): HalphaAnchor {
  const typical = TYPICAL_HALPHA_R[cls];
  if (survey && !survey.beamDiluted && survey.netR > 5) {
    return {
      rayleighs: survey.netR,
      basis: "survey",
      survey,
      note: `Measured from the Finkbeiner all-sky Hα survey: ${survey.netR.toFixed(0)} R above the surroundings. At ${survey.objectArcmin.toFixed(0)}′ the object is larger than the survey's ~1° beam, so this is a real measurement.`,
    };
  }
  if (!typical) {
    return { rayleighs: 0, basis: "none", survey, note: `${OBJECT_CLASS_LABEL[cls]}: no line emission to integrate.` };
  }
  return {
    rayleighs: typical,
    basis: "typical",
    survey,
    note: survey
      ? `At ${survey.objectArcmin.toFixed(0)}′ the object is smaller than the survey's ~1° beam (it reads ${survey.objectR.toFixed(0)} R over the whole region), so the typical ${typical} R for ${classPhrase(cls)} is used. Good to a factor of a few.`
      : `Typical ${typical} R for ${classPhrase(cls)} (survey not measured yet). Good to a factor of a few.`,
  };
}

export type FilterSignal = {
  letter: FilterLetter;
  /** e⁻/s/px at the zenith. */
  zenithRate: number;
  basis: string;
  /** Plain-language explanation of `basis`, for a tooltip. */
  explain: string;
  estimated: boolean;
};

const LINES = [
  { line: "Ha" as const, nm: LINE_WAVELENGTH_NM.Ha },
  { line: "OIII" as const, nm: LINE_WAVELENGTH_NM.OIII },
  { line: "SII" as const, nm: LINE_WAVELENGTH_NM.SII },
];

/** Target zenith signal for every rig filter. */
export function targetSignals(rig: PlanRig, info: CatalogInfo, anchor: HalphaAnchor): FilterSignal[] {
  const cls = info.objectClass;
  const ratios = DEFAULT_LINE_RATIOS[cls];
  const lineR = { Ha: anchor.rayleighs, OIII: anchor.rayleighs * ratios.oiii, SII: anchor.rayleighs * ratios.sii };
  const surf =
    info.surfBr ??
    (info.vmag != null && info.majAxArcmin ? surfaceBrightnessFromMagnitude(info.vmag, info.majAxArcmin, info.minAxArcmin ?? info.majAxArcmin) : null);

  const out: FilterSignal[] = [];
  for (const [letter, filter] of Object.entries(rig.filters) as [FilterLetter, NonNullable<PlanRig["filters"][FilterLetter]>][]) {
    const ctx: SignalContext = { telescope: rig.telescope, camera: rig.camera, filter, pixelScale: rig.pixelScale, altitudeDeg: 90 };
    if (filter.line) {
      const r = lineR[filter.line];
      out.push({
        letter,
        zenithRate: r > 0 ? lineSignalRate(ctx, r) : 0,
        basis:
          filter.line === "Ha"
            ? `${r.toFixed(0)} R Hα (${anchor.basis})`
            : `${r.toFixed(0)} R = Hα × ${(filter.line === "OIII" ? ratios.oiii : ratios.sii).toFixed(2)} (class ratio)`,
        explain:
          filter.line === "Ha"
            ? `Estimated brightness of the target's Hα emission, in Rayleighs (R), used to predict the SNR this filter reaches. ${anchor.note}`
            : `Estimated ${filter.line} brightness in Rayleighs (R). It is not measured: it is the Hα estimate times the line ratio typical of ${classPhrase(cls)} (${(filter.line === "OIII" ? ratios.oiii : ratios.sii).toFixed(2)}), so real targets can differ a lot.`,
        estimated: true,
      });
      continue;
    }
    // Broadband: mean continuum plus any emission line inside the passband.
    let rate = surf != null ? broadbandSignalRate(ctx, surf) : 0;
    const lo = filter.centerNm - filter.fwhmNm / 2;
    const hi = filter.centerNm + filter.fwhmNm / 2;
    const inBand = emitsLines(cls) || cls === "galaxy" ? LINES.filter((l) => l.nm >= lo && l.nm <= hi && lineR[l.line] > 0) : [];
    for (const l of inBand) rate += lineSignalRate({ ...ctx, filter: { ...filter, centerNm: l.nm } }, lineR[l.line]);
    const parts = [surf != null ? `${surf.toFixed(1)} mag/″² continuum` : null, inBand.length ? `${inBand.map((l) => l.line).join(" + ")} lines` : null].filter(Boolean);
    const band = `${Math.round(lo)}–${Math.round(hi)} nm`;
    const explain = [
      "What the SNR model assumes this broadband filter collects from the target.",
      surf != null
        ? `Continuum: the catalog's mean surface brightness, ${surf.toFixed(1)} mag/arcsec² (starlight and reflected light, spread evenly over the object).`
        : "Continuum: none — the catalog gives no magnitude or surface brightness for this object.",
      inBand.length
        ? `Emission: the ${inBand.map((l) => `${l.line === "Ha" ? "Hα" : l.line} (${l.nm.toFixed(0)} nm)`).join(" and ")} line${inBand.length > 1 ? "s" : ""} of the target fall${inBand.length > 1 ? "" : "s"} inside this filter's ${band} passband and add${inBand.length > 1 ? "" : "s"} to its signal.`
        : `Emission: none of the modelled lines (Hα, OIII, SII) falls inside this filter's ${band} passband.`,
      parts.length ? null : "With neither, the predicted signal is zero, so an SNR goal cannot be computed for this filter; use an hours goal.",
    ]
      .filter(Boolean)
      .join(" ");
    out.push({ letter, zenithRate: rate, basis: parts.length ? parts.join(" + ") : "no broadband signal known", explain, estimated: true });
  }
  return out;
}

/** The filters that make sense for a class when the target has no data or goals yet. */
export function classFilters(cls: ObjectClass, owned: FilterLetter[]): FilterLetter[] {
  const want: FilterLetter[] =
    cls === "galaxy" || cls === "reflection" || cls === "cluster" || cls === "dark" || cls === "star"
      ? ["L", "R", "G", "B"]
      : cls === "planetary"
        ? ["O", "H", "S"]
        : ["H", "O", "S"];
  const have = want.filter((f) => owned.includes(f));
  return have.length ? have : want;
}

/** "an HII region", "a planetary nebula": the class label in running text. */
function classPhrase(cls: ObjectClass): string {
  const label = OBJECT_CLASS_LABEL[cls];
  const text = /^[A-Z]{2}/.test(label) ? label : label.toLowerCase();
  return `${/^[aeiouAEIOU]|^HII/.test(text) ? "an" : "a"} ${text}`;
}
