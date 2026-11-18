/**
 * Goals per filter (PLAN §6.4): a number of hours, or Match — as good as a reference target
 * in that filter. No heavy imports: used in the browser too.
 *
 * Match is measured, not modelled: the engine (api_match.py) measures the quality each target
 * gathers per hour in each filter from its own kept subs (nebula contrast over the noise,
 * squared, so it adds up over subs). The reference's quality is its rate times its good hours;
 * the hours this target needs are that total over this target's own rate.
 */

import type { FilterLetter } from "./rig";
import type { Goal, MatchFilter } from "./types";

/** A reference target as a Match goal sees it. */
export type MatchRef = {
  id: string;
  title: string;
  goodHours: number;
  measured: MatchFilter | null;
  scale: number | null;
};

export type MatchInput = {
  /** This target's measurement in this filter (null: no analysed night with this filter yet). */
  measured: MatchFilter | null;
  scale: number | null;
  ref: MatchRef | null;
};

export type GoalStatus = {
  letter: FilterLetter;
  goal: Goal | null;
  goodHours: number;
  rawHours: number;
  /** Hours the goal stands for (hours mode: as set; match mode: the measured equivalent, or null until measurable). */
  goalHours: number | null;
  remainingHours: number | null;
  need: number;
  flag: string | null;
  /** Match only: the reference, and how many of its hours one of this target's equals (> 1: this target needs more). */
  ref: MatchRef | null;
  factor: number | null;
  /** Match only: why there are no hours yet. */
  pending: string | null;
};

export function goalStatus(
  letter: FilterLetter,
  goal: Goal | null,
  goodSeconds: number,
  rawSeconds: number,
  capHours: number,
  seasonHoursLeft: number | null,
  match: MatchInput | null = null,
): GoalStatus {
  const goodHours = goodSeconds / 3600;
  let goalHours: number | null = null;
  let factor: number | null = null;
  let pending: string | null = null;
  let flag: string | null = null;
  const ref = goal?.mode === "match" ? (match?.ref ?? null) : null;
  if (goal?.mode === "hours" && goal.hours != null) {
    goalHours = goal.hours;
  } else if (goal?.mode === "match") {
    const own = match?.measured ?? null;
    if (!ref) pending = "The reference target is no longer in the Library.";
    else if (!ref.measured || ref.measured.qPerHour <= 0) pending = `${ref.title} has no analysed night in this filter to compare with.`;
    else if (!own) pending = "Needs one analysed night in this filter.";
    else if (own.qPerHour <= 0) pending = "No nebula signal measured in this filter yet.";
    else {
      factor = ref.measured.qPerHour / own.qPerHour;
      goalHours = factor * ref.goodHours;
      if (ref.scale && match?.scale && Math.abs(ref.scale / match.scale - 1) > 0.1)
        flag = `Different pixel scale (${match.scale.toFixed(2)}″ vs ${ref.scale.toFixed(2)}″/px): the comparison is approximate.`;
    }
  }
  const remaining = goalHours != null ? Math.max(0, goalHours - goodHours) : null;
  if (goal?.mode === "match" && goalHours != null) {
    if (goalHours > capHours) flag = `Implies ${goalHours.toFixed(0)} h, more than the ${capHours} h cap.`;
    else if (seasonHoursLeft != null && remaining != null && remaining > seasonHoursLeft)
      flag = `Needs ${remaining.toFixed(0)} h more, but only ${seasonHoursLeft.toFixed(0)} usable h are left in the next 90 nights here.`;
  }
  return {
    letter,
    goal,
    goodHours,
    rawHours: rawSeconds / 3600,
    goalHours,
    remainingHours: remaining,
    need: goalHours && goalHours > 0 && remaining != null ? Math.min(1, remaining / goalHours) : 0,
    flag,
    ref,
    factor,
    pending,
  };
}
