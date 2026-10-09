// VENDORED from D:\Python\AstroPlanner\src\lib\snr$n (2026-10-09), unmodified.
// AstroDashboard owns this copy; it is pinned by scripts/verify-snr.ts. Do not import from the sibling at runtime.
/**
 * Emission line ratios by object class.
 *
 * THESE ARE ESTIMATES, and they are the largest single source of error in an
 * OIII or SII prediction. There is no all-sky OIII or SII survey to measure
 * them from — only H-alpha (Finkbeiner) is available calibrated and full-sky —
 * so OIII and SII signal is derived from H-alpha through these ratios.
 *
 * They are defaults, not constants: every one is overridable per target and
 * persisted, and the UI must present them as adjustable estimates rather than
 * as measured properties of the object.
 *
 * Values are typical published line ratios relative to H-alpha for each class.
 */

export type ObjectClass =
  | "hii"
  | "planetary"
  | "snr"
  | "wolfrayet"
  | "reflection"
  | "dark"
  | "galaxy"
  | "cluster"
  | "star"
  | "unknown";

export type LineRatios = {
  /** OIII 5007 / H-alpha */
  oiii: number;
  /** SII 6717+6731 / H-alpha */
  sii: number;
};

export const DEFAULT_LINE_RATIOS: Record<ObjectClass, LineRatios> = {
  // Photoionised by hot young stars: strong Ha, modest OIII except near the
  // exciting star, modest SII.
  hii: { oiii: 0.15, sii: 0.12 },
  // High excitation and often OIII-dominated — the Ring and Dumbbell are
  // brighter in OIII than in Ha.
  planetary: { oiii: 3.0, sii: 0.05 },
  // Shock-excited. SII/Ha > 0.4 is the classic diagnostic that separates a
  // remnant from an HII region.
  snr: { oiii: 0.7, sii: 0.5 },
  // Wind-blown bubble around a Wolf-Rayet star: a bright inner OIII shell and
  // strong SII in the swept-up rim. NGC 6888 is the type case.
  wolfrayet: { oiii: 0.5, sii: 0.35 },
  // Scattered starlight, essentially no line emission. Narrowband is the wrong
  // tool; these want broadband.
  reflection: { oiii: 0, sii: 0 },
  // Absorption, not emission. There is no signal to integrate here at all —
  // a dark nebula is seen as an absence against what lies behind it, which the
  // SNR model cannot represent. The UI must say so rather than print a number.
  dark: { oiii: 0, sii: 0 },
  galaxy: { oiii: 0.1, sii: 0.1 },
  cluster: { oiii: 0, sii: 0 },
  star: { oiii: 0, sii: 0 },
  unknown: { oiii: 0.2, sii: 0.15 },
};

/** Whether narrowband imaging makes sense for this class at all. */
export function emitsLines(cls: ObjectClass): boolean {
  const r = DEFAULT_LINE_RATIOS[cls];
  return r.oiii > 0 || r.sii > 0;
}

/**
 * Objects whose catalog type is right but too coarse for line prediction.
 *
 * OpenNGC files wind-blown bubbles around Wolf-Rayet stars as plain "HII",
 * which would hand them an OIII/Ha of 0.15 — badly wrong for exactly the
 * targets whose bright inner OIII shell is the reason people image them. NGC
 * 6888 is the type case: shot as HII it looks like OIII is hopeless, when in
 * reality OIII is the whole point.
 */
const CLASS_OVERRIDES: Record<string, ObjectClass> = {
  "NGC 6888": "wolfrayet", // Crescent
  "NGC 2359": "wolfrayet", // Thor's Helmet
  "NGC 3199": "wolfrayet",
  "NGC 6164": "wolfrayet",
  "Sh2-308": "wolfrayet", // Dolphin Head
  "Sh2-157": "hii",
  "NGC 6979": "snr", // Veil complex components filed as emission nebulae
  "NGC 6974": "snr",
  "IC 1340": "snr",
};

/**
 * Maps a catalog type code to an object class.
 * OpenNGC uses short codes; Sharpless/LBN entries are HII regions by definition.
 */
export function classifyType(type: string, name = ""): ObjectClass {
  const override = CLASS_OVERRIDES[name.trim()];
  if (override) return override;

  const t = type.trim().toLowerCase();
  const n = name.trim().toLowerCase();

  if (t === "drkn") return "dark";
  if (n.startsWith("sh2") || n.startsWith("lbn")) return "hii";
  if (t === "pn") return "planetary";
  if (t === "snr") return "snr";
  if (t === "emn" || t === "hii" || t === "neb" || t === "cl+n") return "hii";
  if (t === "rfn") return "reflection";
  if (t === "g" || t === "gpair" || t === "gtrpl" || t === "ggroup") return "galaxy";
  if (t === "ocl" || t === "gcl") return "cluster";
  if (t === "*" || t === "**" || t === "*ass") return "star";
  return "unknown";
}

/** Human label for the UI. */
export const OBJECT_CLASS_LABEL: Record<ObjectClass, string> = {
  hii: "HII region",
  planetary: "Planetary nebula",
  snr: "Supernova remnant",
  wolfrayet: "Wolf-Rayet bubble",
  reflection: "Reflection nebula",
  dark: "Dark nebula",
  galaxy: "Galaxy",
  cluster: "Star cluster",
  star: "Star",
  unknown: "Unclassified",
};
