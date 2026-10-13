"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Save } from "lucide-react";

import { goalStatus } from "@/lib/planning/goals";
import type { FilterLetter } from "@/lib/planning/rig";
import type { Goal, GoalMode } from "@/lib/planning/types";
import { FilterChip, StatusIcon, Tip } from "../ui";
import styles from "./goals.module.css";

export type GoalRow = {
  letter: FilterLetter;
  name: string;
  goal: Goal | null;
  goodSeconds: number;
  rawSeconds: number;
  /** SNR² per hour under this site's dark sky at 50° (the model's conversion rate). */
  rate: number;
  /** Basis of the target signal in this filter, shown under the row. */
  basis: string;
  /** What `basis` means, shown when hovering it. */
  basisTip: string;
};

type Draft = { mode: GoalMode; hours: string; snr: string };

const fmt = (v: number | null | undefined, d = 1) => (v == null ? "—" : Number.isFinite(v) ? v.toFixed(d) : "∞");
const toDraft = (g: Goal | null): Draft => ({ mode: g?.mode ?? "none", hours: g?.hours != null ? String(g.hours) : "", snr: g?.snr != null ? String(g.snr) : "" });

/**
 * Goals per filter, each with its own mode (PLAN §6.4). Both numbers are always
 * shown: an SNR goal shows the hours it implies, an hours goal the SNR it reaches.
 * Switching mode pre-fills the other number from the model (capped), so an
 * override is one edit. Saved to the dashboard database, never to the data root.
 */
export function GoalsEditor({
  revId,
  rows,
  capHours,
  seasonHours,
  siteName,
}: {
  revId: string;
  rows: GoalRow[];
  capHours: number;
  seasonHours: number;
  siteName: string;
}) {
  const router = useRouter();
  const initial = useMemo(() => Object.fromEntries(rows.map((r) => [r.letter, toDraft(r.goal)])) as Record<string, Draft>, [rows]);
  const [draft, setDraft] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const goalOf = (d: Draft): Goal | null => {
    if (d.mode === "hours" && d.hours !== "" && Number(d.hours) >= 0) return { mode: "hours", hours: Number(d.hours) };
    if (d.mode === "snr" && d.snr !== "" && Number(d.snr) > 0) return { mode: "snr", snr: Number(d.snr) };
    if (d.mode === "none") return { mode: "none" };
    return null;
  };
  const snapshot = (src: Record<string, Draft>) => JSON.stringify(rows.map((r) => goalOf(src[r.letter])));
  const dirty = snapshot(draft) !== snapshot(initial);

  const setMode = (r: GoalRow, mode: GoalMode) => {
    const d = draft[r.letter];
    const st = goalStatus(r.letter, goalOf(d), r.goodSeconds, r.rawSeconds, r.rate, capHours, seasonHours);
    const next: Draft = { ...d, mode };
    if (mode === "hours" && d.hours === "") {
      const h = st.goalHours != null && Number.isFinite(st.goalHours) ? Math.min(st.goalHours, capHours) : capHours / 4;
      next.hours = String(Math.round(h * 2) / 2);
    }
    if (mode === "snr" && d.snr === "") {
      // From hours: the SNR those hours reach. From nothing: a round target above what you already have.
      const s = st.goalSnr ?? Math.max(20, Math.ceil(((st.snrSoFar ?? 0) * 1.5) / 5) * 5);
      next.snr = String(Math.round(s));
    }
    setDraft({ ...draft, [r.letter]: next });
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    const goals: Record<string, Goal> = {};
    for (const r of rows) {
      const g = goalOf(draft[r.letter]);
      if (draft[r.letter].mode !== "none" && !g) {
        setError(`${r.name}: enter a value or choose "None".`);
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
            <th className="num">SNR</th>
            <th>Progress (good hours)</th>
            <th>Check</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const d = draft[r.letter];
            const st = goalStatus(r.letter, goalOf(d), r.goodSeconds, r.rawSeconds, r.rate, capHours, seasonHours);
            const frac = st.goalHours && Number.isFinite(st.goalHours) ? Math.min(1, st.goodHours / st.goalHours) : 0;
            return (
              <tr key={r.letter}>
                <td>
                  <FilterChip filter={r.letter} /> {r.name}
                  <div className={styles.basis}>
                    {r.basisTip ? <Tip tip={r.basisTip}>{r.basis}</Tip> : r.basis}
                  </div>
                </td>
                <td>
                  <div className={styles.modes} role="group" aria-label={`${r.name} goal mode`}>
                    {(["hours", "snr", "none"] as const).map((m) => (
                      <button key={m} type="button" className={d.mode === m ? `${styles.mode} ${styles.modeOn}` : styles.mode} aria-pressed={d.mode === m} onClick={() => setMode(r, m)}>
                        {m === "snr" ? "SNR" : m === "hours" ? "Hours" : "None"}
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
                      onChange={(e) => {
                        setDraft({ ...draft, [r.letter]: { ...d, hours: e.target.value } });
                        setSaved(false);
                      }}
                    />
                  ) : (
                    <span className={styles.implied}>{d.mode === "snr" ? fmt(st.goalHours) : "—"}</span>
                  )}
                </td>
                <td className="num">
                  {d.mode === "snr" ? (
                    <input
                      className={styles.input}
                      type="number"
                      min={1}
                      step={1}
                      value={d.snr}
                      aria-label={`${r.name} goal SNR`}
                      onChange={(e) => {
                        setDraft({ ...draft, [r.letter]: { ...d, snr: e.target.value } });
                        setSaved(false);
                      }}
                    />
                  ) : (
                    <span className={styles.implied}>{d.mode === "hours" ? fmt(st.goalSnr) : "—"}</span>
                  )}
                </td>
                <td>
                  <div className={styles.progress}>
                    <span className={styles.bar}>
                      <span style={{ width: `${Math.round(frac * 100)}%` }} />
                    </span>
                    <span className="num">
                      {st.goodHours.toFixed(1)}
                      {st.goalHours != null ? ` / ${fmt(st.goalHours)}` : ""} h
                    </span>
                  </div>
                  <div className={styles.basis}>
                    SNR so far ≈ {fmt(st.snrSoFar)}
                    {st.rawHours - st.goodHours > 0.05 ? ` · ${(st.rawHours - st.goodHours).toFixed(1)} h rejected by diagnostics` : ""}
                  </div>
                </td>
                <td>
                  {st.flag ? (
                    <div className={styles.flag}>
                      <StatusIcon level="warn" size={13} />
                      <span>
                        {st.flag}{" "}
                        <button
                          type="button"
                          className={styles.link}
                          onClick={() => {
                            setDraft({ ...draft, [r.letter]: { mode: "hours", hours: String(capHours), snr: d.snr } });
                            setSaved(false);
                          }}
                        >
                          Switch to hours ({capHours} h)
                        </button>
                        <br />
                        <span className={styles.basis}>Likely cause: {r.letter === "O" || r.letter === "S" ? "the OIII/SII ratio estimated from the object class" : "a beam-diluted or typical Hα estimate, or the sky model"}.</span>
                      </span>
                    </div>
                  ) : st.goal && st.goal.mode !== "none" && st.remainingHours === 0 ? (
                    <StatusIcon level="ok" size={13} label="Goal reached" />
                  ) : st.remainingHours != null ? (
                    <span className={styles.basis}>{st.remainingHours.toFixed(1)} h to go</span>
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
          Hours ↔ SNR use {siteName}&apos;s sky (moonless, target at 50°) and your sub lengths. Cap: {capHours} h per filter (Settings → Goals).
        </span>
      </div>
    </div>
  );
}
