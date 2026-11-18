"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Save } from "lucide-react";

import { hm } from "@/lib/format";
import { goalStatus, type MatchRef } from "@/lib/planning/goals";
import type { FilterLetter } from "@/lib/planning/rig";
import type { Goal, GoalMode, MatchFilter } from "@/lib/planning/types";
import { FilterChip, StatusIcon, Tip } from "../ui";
import styles from "./goals.module.css";

/** A Library target that can be a Match reference in one filter. */
export type MatchCandidate = MatchRef & { subtitle: string; nights: number; lastNight: string | null };

export type GoalRow = {
  letter: FilterLetter;
  name: string;
  goal: Goal | null;
  goodSeconds: number;
  rawSeconds: number;
  /** This target's Match measurement in this filter (null: no analysed night with it yet). */
  measured: MatchFilter | null;
  scale: number | null;
  /** Targets with measured data in this filter, most similar in brightness first (this one excluded). */
  candidates: MatchCandidate[];
};

type Draft = { mode: GoalMode; hours: string; ref: string };

const toDraft = (g: Goal | null): Draft => ({ mode: g?.mode ?? "none", hours: g?.hours != null ? String(g.hours) : "", ref: g?.ref ?? "" });
/** ×12, ×1.8, ×0.35, ×0.012: two significant digits, never "×0.0". */
const times = (f: number) => (f >= 10 ? f.toFixed(0) : f.toPrecision(2).replace(/\.?0+$/, ""));

/** How a candidate compares with this target in the filter, for the reference list. */
const similarity = (ratio: number) => (ratio >= 0.8 && ratio <= 1.25 ? "similar" : ratio > 1 ? `${times(ratio)}× stronger` : `${times(1 / ratio)}× weaker`);

/** A reference much brighter or fainter in this filter: say what Match then means. */
function contrastNote(factor: number, ref: string): string | null {
  if (factor < 0.2) return `${ref} is ${times(1 / factor)}× weaker in this filter (nebula contrast over noise per hour), so matching it is a low bar here.`;
  if (factor > 5) return `${ref} is ${times(factor)}× stronger in this filter, so matching it takes this target much longer.`;
  return null;
}

/**
 * Goals per filter, each with its own mode (PLAN §6.4): Hours as set, or Match — as good as
 * a reference target in that filter. Match hours come from both targets' own subs (goals.ts);
 * until this target has an analysed night in the filter there is nothing to compare, and the
 * filter shows why. Saved to the dashboard database, never to the data root.
 */
export function GoalsEditor({
  revId,
  rows,
  capHours,
  seasonHours,
  measuring,
}: {
  revId: string;
  rows: GoalRow[];
  capHours: number;
  seasonHours: number;
  /** The engine is still measuring frames for Match (this target or a reference). */
  measuring?: string | null;
}) {
  const router = useRouter();
  const initial = useMemo(() => Object.fromEntries(rows.map((r) => [r.letter, toDraft(r.goal)])) as Record<string, Draft>, [rows]);
  const [draft, setDraft] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const goalOf = (d: Draft): Goal | null => {
    if (d.mode === "hours" && d.hours !== "" && Number(d.hours) >= 0) return { mode: "hours", hours: Number(d.hours) };
    if (d.mode === "match" && d.ref) return { mode: "match", ref: d.ref };
    if (d.mode === "none") return { mode: "none" };
    return null;
  };
  const snapshot = (src: Record<string, Draft>) => JSON.stringify(rows.map((r) => goalOf(src[r.letter])));
  const dirty = snapshot(draft) !== snapshot(initial);

  const statusOf = (r: GoalRow, d: Draft) =>
    goalStatus(r.letter, goalOf(d), r.goodSeconds, r.rawSeconds, capHours, seasonHours, {
      measured: r.measured,
      scale: r.scale,
      ref: r.candidates.find((c) => c.id === d.ref) ?? null,
    });

  const update = (r: GoalRow, next: Partial<Draft>) => {
    const d = { ...draft[r.letter], ...next };
    if (next.mode === "hours" && d.hours === "") {
      // From Match: the hours it stood for. From nothing: a quarter of the cap.
      const h = statusOf(r, draft[r.letter]).goalHours ?? capHours / 4;
      d.hours = String(Math.round(Math.min(h, capHours) * 2) / 2);
    }
    setDraft({ ...draft, [r.letter]: d });
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    const goals: Record<string, Goal> = {};
    for (const r of rows) {
      const d = draft[r.letter];
      const g = goalOf(d);
      if (d.mode !== "none" && !g) {
        setError(d.mode === "match" ? `${r.name}: choose a reference target or "None".` : `${r.name}: enter hours or choose "None".`);
        setSaving(false);
        return;
      }
      if (g && !(g.mode === "none" && r.goal == null)) goals[r.letter] = g;
    }
    try {
      const res = await fetch(`/engine/api/goals/${encodeURIComponent(revId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goals }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Engine error ${res.status}`);
      setSaved(true);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <table className="table">
        <thead>
          <tr>
            <th>Filter</th>
            <th>Goal</th>
            <th className="num">Hours</th>
            <th>Reference</th>
            <th>Progress (good time)</th>
            <th>Check</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const d = draft[r.letter];
            const st = statusOf(r, d);
            const frac = st.goalHours ? Math.min(1, st.goodHours / st.goalHours) : 0;
            return (
              <tr key={r.letter}>
                <td>
                  <FilterChip filter={r.letter} /> {r.name}
                  <div className={styles.basis}>
                    {r.measured ? (
                      <Tip tip={`Measured for Match from ${r.measured.frames} kept subs over ${r.measured.nights} night${r.measured.nights > 1 ? "s" : ""}: the nebula's contrast over the noise, per hour.`}>
                        measured · {r.measured.nights} night{r.measured.nights > 1 ? "s" : ""}
                      </Tip>
                    ) : (
                      "not measured yet"
                    )}
                  </div>
                </td>
                <td>
                  <div className={styles.modes} role="group" aria-label={`${r.name} goal mode`}>
                    {(["hours", "match", "none"] as const).map((m) => (
                      <button key={m} type="button" className={d.mode === m ? `${styles.mode} ${styles.modeOn}` : styles.mode} aria-pressed={d.mode === m} onClick={() => update(r, { mode: m })}>
                        {m === "match" ? "Match" : m === "hours" ? "Hours" : "None"}
                      </button>
                    ))}
                  </div>
                </td>
                <td className="num">
                  {d.mode === "hours" ? (
                    <input
                      className={styles.input}
                      type="number"
                      min={0}
                      step={0.5}
                      value={d.hours}
                      aria-label={`${r.name} goal hours`}
                      onChange={(e) => update(r, { hours: e.target.value })}
                    />
                  ) : (
                    <span className={styles.implied}>{d.mode === "match" && st.goalHours != null ? hm(st.goalHours) : "—"}</span>
                  )}
                </td>
                <td>
                  {d.mode === "match" ? (
                    <>
                      <select className={styles.select} value={d.ref} aria-label={`${r.name} reference target`} onChange={(e) => update(r, { ref: e.target.value })}>
                        <option value="">{r.candidates.length ? "Choose a target…" : "No measured target in this filter"}</option>
                        {r.candidates.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.title}
                            {c.subtitle ? ` ${c.subtitle}` : ""} · {hm(c.goodHours)} · {c.nights} night{c.nights > 1 ? "s" : ""}
                            {r.measured && c.measured ? ` · ${similarity(c.measured.qPerHour / r.measured.qPerHour)}` : ""}
                          </option>
                        ))}
                        {d.ref && !r.candidates.some((c) => c.id === d.ref) ? <option value={d.ref}>{d.ref} (not measured)</option> : null}
                      </select>
                      <div className={styles.basis}>
                        {st.factor != null && st.ref
                          ? `${hm(st.ref.goodHours)} on ${st.ref.title} · this target needs ×${times(st.factor)} the hours. ${contrastNote(st.factor, st.ref.title) ?? ""}`.trim()
                          : d.ref
                            ? st.pending
                            : "Its quality in this filter becomes the goal."}
                      </div>
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <div className={styles.progress}>
                    <span className={styles.bar}>
                      <span style={{ width: `${Math.round(frac * 100)}%` }} />
                    </span>
                    <span className="num">
                      {hm(st.goodHours)}
                      {st.goalHours != null ? ` / ${hm(st.goalHours)}` : ""}
                    </span>
                  </div>
                  {st.rawHours - st.goodHours > 0.05 ? <div className={styles.basis}>{hm(st.rawHours - st.goodHours)} rejected by diagnostics</div> : null}
                </td>
                <td>
                  {st.flag ? (
                    <div className={styles.flag}>
                      <StatusIcon level="warn" size={13} />
                      <span>{st.flag}</span>
                    </div>
                  ) : st.goal && st.goal.mode !== "none" && st.remainingHours === 0 ? (
                    <StatusIcon level="ok" size={13} label="Goal reached" />
                  ) : st.remainingHours != null ? (
                    <span className={styles.basis}>{hm(st.remainingHours)} to go</span>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className={styles.actions}>
        <button type="button" className="button button-primary" disabled={!dirty || saving} onClick={save}>
          <Save size={13} strokeWidth={1.8} /> {saving ? "Saving…" : "Save goals"}
        </button>
        {saved && !dirty ? <span className={styles.basis}>Saved.</span> : null}
        {error ? <span className={styles.error}>{error}</span> : null}
        <span className={styles.basis}>
          Match: as good as another target in that filter. Both are measured from their kept subs (nebula contrast over the noise), so the hours follow
          from yours. Cap: {capHours} h per filter (Settings → Goals).
          {measuring ? ` Measuring ${measuring}…` : ""}
        </span>
      </div>
    </div>
  );
}
