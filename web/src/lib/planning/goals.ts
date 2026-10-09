/** Goals per filter: hours ↔ SNR, both always shown (PLAN §6.4). No heavy imports: used in the browser too. */

import type { FilterLetter } from "./rig";
import type { Goal } from "./types";

export type GoalStatus = {
  letter: FilterLetter;
  goal: Goal | null;
  goodHours: number;
  rawHours: number;
  /** Hours the goal stands for (hours mode: as set; SNR mode: implied by the model). */
  goalHours: number | null;
  /** SNR the goal stands for (SNR mode: as set; hours mode: predicted at goalHours). */
  goalSnr: number | null;
  /** SNR predicted for the good hours already collected. */
  snrSoFar: number | null;
  remainingHours: number | null;
  need: number;
  flag: string | null;
};

/** Goal ↔ hours ↔ SNR for one filter (both numbers always shown, PLAN §6.4). */
export function goalStatus(
  letter: FilterLetter,
  goal: Goal | null,
  goodSeconds: number,
  rawSeconds: number,
  rate: number,
  capHours: number,
  seasonHoursLeft: number | null,
): GoalStatus {
  const goodHours = goodSeconds / 3600;
  const snrFor = (h: number) => (rate > 0 ? Math.sqrt(rate * h) : null);
  let goalHours: number | null = null;
  let goalSnr: number | null = null;
  if (goal?.mode === "hours" && goal.hours != null) {
    goalHours = goal.hours;
    goalSnr = snrFor(goal.hours);
  } else if (goal?.mode === "snr" && goal.snr != null) {
    goalSnr = goal.snr;
    goalHours = rate > 0 ? (goal.snr * goal.snr) / rate : Infinity;
  }
  const remaining = goalHours != null ? Math.max(0, goalHours - goodHours) : null;
  let flag: string | null = null;
  if (goal?.mode === "snr" && goalHours != null) {
    if (!Number.isFinite(goalHours)) flag = "The model predicts no signal in this filter for this target.";
    else if (goalHours > capHours) flag = `Implies ${goalHours.toFixed(0)} h, more than the ${capHours} h cap.`;
    else if (seasonHoursLeft != null && remaining != null && remaining > seasonHoursLeft)
      flag = `Needs ${remaining.toFixed(0)} h more, but only ${seasonHoursLeft.toFixed(0)} usable h are left in the next 90 nights here.`;
  }
  return {
    letter,
    goal,
    goodHours,
    rawHours: rawSeconds / 3600,
    goalHours,
    goalSnr,
    snrSoFar: snrFor(goodHours),
    remainingHours: remaining,
    need: goalHours && Number.isFinite(goalHours) && goalHours > 0 && remaining != null ? Math.min(1, remaining / goalHours) : 0,
    flag,
  };
}
