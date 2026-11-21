/**
 * One filter order for the whole night, every filter you own, each with its reason.
 *
 * When Astrospheric covers the night, your sky rules lead (skyrule.ts): the
 * night's sky class puts its filters first, in the rule's order. The rest —
 * or every filter, without a forecast — follow by what an hour is worth in
 * that filter tonight, weighed by opportunity: with the moon down most of the
 * night the filters a bright moon hurts most come first (dark time is scarce
 * for them); with the moon up, the ones that keep the most of a moonless hour.
 * That is the same value as a target's score (plan.ts `filterValue`), with no
 * goal term: goals are per target and stay on each target's row.
 *
 * Efficiencies are means over your active targets that are up tonight, weighted
 * by their usable hours, so the moon's separation is that of what you'd shoot.
 */

import type { FilterNight } from "./evaluate";
import { FILTER_NAMES, type FilterLetter } from "./rig";
import { SKY_LABEL, SKY_ORDER, skyClass, type NightSky, type SkyClass } from "./skyrule";

export type FilterPriority = {
  letter: FilterLetter;
  /** 0–1: tonight's hour against a moonless one; null when no target is up to judge it. */
  efficiency: number | null;
  /** 0–1: what a reference bright moon (75 % lit, 60° away) costs this filter. */
  sensitivity: number;
  /** Placed by your sky rule (true) or by the moon (false). */
  byRule: boolean;
  reason: string;
};

export type NightPriority = {
  sky: SkyClass | null;
  /** One line on why the list is in this order. */
  summary: string;
  filters: FilterPriority[];
};

const pct = (x: number) => `${Math.round(x * 100)} %`;

/** A night is "dark" for the order when the moon is down at least this share of it. */
const DARK_SHARE = 0.5;

export function nightPriority(input: {
  owned: FilterLetter[];
  /** Night-level evaluation (one per owned filter); empty when nothing is up. */
  evals: FilterNight[];
  sensitivity: Partial<Record<FilterLetter, number>>;
  /** Share of the night's darkness with the moon down. */
  moonDownFraction: number;
  moonIllum: number;
  sky: NightSky | null;
}): NightPriority {
  const { owned, evals, sensitivity, moonDownFraction, moonIllum, sky } = input;
  const effOf = (l: FilterLetter) => evals.find((f) => f.letter === l)?.efficiency;
  const sens = (l: FilterLetter) => sensitivity[l] ?? 0;
  const dark = moonDownFraction >= DARK_SHARE || moonIllum < 0.05;
  const value = (l: FilterLetter) => (effOf(l) ?? 1) * Math.max(0, 1 + (2 * moonDownFraction - 1) * sens(l));

  const rule = sky && evals.length ? skyClass(sky, effOf) : null;
  const lead = rule ? SKY_ORDER[rule.cls].filter((l) => owned.includes(l)) : [];
  const rest = owned.filter((l) => !lead.includes(l)).sort((a, b) => value(b) - value(a) || a.localeCompare(b));

  const out: FilterPriority[] = [];
  lead.forEach((l, i) => {
    const label = SKY_LABEL[rule!.cls];
    out.push({
      letter: l,
      efficiency: effOf(l) ?? null,
      sensitivity: sens(l),
      byRule: true,
      reason: i === 0 ? `Your ${label} rule puts it first` : `Next in the ${label} order (${lead.slice(0, i).map((x) => FILTER_NAMES[x]).join(", ")} first)`,
    });
  });
  for (const l of rest) {
    const e = effOf(l);
    let reason: string;
    if (e == null) reason = "Nothing of yours is up tonight to judge it";
    else if (dark) {
      reason = sens(l) >= 0.25
        ? `Moon down most of the night; a bright moon costs it ${pct(sens(l))}, so the dark hours are worth more to it`
        : `Copes with a bright moon (it costs it ${pct(sens(l))}): keep it for moonlit nights`;
    } else reason = `An hour tonight is worth ${pct(e)} of a moonless one`;
    out.push({ letter: l, efficiency: e ?? null, sensitivity: sens(l), byRule: false, reason });
  }

  const moonPart = moonIllum < 0.05
    ? "no moon"
    : dark
      ? `moon ${pct(moonIllum)} lit but down ${pct(moonDownFraction)} of the night`
      : `moon ${pct(moonIllum)} lit, up ${pct(1 - moonDownFraction)} of the night`;
  let summary: string;
  if (rule) {
    const after = rest.length ? `; then by what the moon costs each filter (${moonPart})` : "";
    summary = `${SKY_LABEL[rule.cls]} (${rule.why.join(", ")}): your rule leads${after}.`;
  } else if (!evals.length) {
    summary = `None of your active targets is up tonight; ordered by how much a bright moon costs each filter (${moonPart}).`;
  } else {
    summary = `No Astrospheric forecast for this night, so your sky rules can't apply; ordered by what the moon costs each filter (${moonPart}).`;
  }
  return { sky: rule?.cls ?? null, summary, filters: out };
}
