"use client";

import { useEffect, useState } from "react";

import { framingBusy, framingDetails, framingLevel, framingText, type FrameFraming, type SessionFraming } from "@/lib/framing";
import { StatusIcon } from "../ui";
import styles from "./Diagnostics.module.css";
import findingStyles from "./Findings.module.css";

type State = { data: SessionFraming | null; error: string | null };

/** A night's framing against the previous night; asked again while the engine measures its lights. */
export function useSessionFraming(revId: string, folder: string, refresh?: unknown): State {
  const [state, setState] = useState<State>({ data: null, error: null });
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const res = await fetch(`/engine/api/framing/sessions/${encodeURIComponent(revId)}/${encodeURIComponent(folder)}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`engine error ${res.status}`);
        const data = (await res.json()) as SessionFraming;
        if (stop) return;
        setState({ data, error: null });
        if (framingBusy(data.job)) timer = setTimeout(load, 1500);
      } catch (e) {
        if (stop) return;
        setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
        timer = setTimeout(load, 10_000);
      }
    }
    void load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [revId, folder, refresh]);
  return state;
}

/** Every light's framing, by Diagnostics frame path (all panels). */
export function framesOf(data: SessionFraming | null): Record<string, FrameFraming> {
  return Object.assign({}, ...(data?.panels ?? []).map((p) => p.frames));
}

/** Summary: the night against the previous one, per mosaic panel (`panel`: only that one). */
export function FramingBlock({ state, panel }: { state: State; panel: string | null }) {
  const { data, error } = state;
  const rows = (data?.panels ?? []).filter((p) => !panel || p.panel === panel);
  const job = data?.job;
  return (
    <section className={styles.block}>
      <div className={styles.blockHead}>
        <h2 className="section-title">Framing</h2>
        <span className="eyebrow">vs the previous night · shift and rotation at the corners, against the dither</span>
      </div>
      {!data ? (
        <p className="muted">{error ? `Framing unavailable: ${error}` : "Loading…"}</p>
      ) : (
        <ul className={findingStyles.list}>
          {rows.map((r) => {
            const v = r.verdict;
            const counts = { ok: 0, warn: 0, bad: 0 };
            for (const f of Object.values(r.frames)) counts[f.level] += 1;
            const outliers = Object.values(r.frames).filter((f) => f.outlier).length;
            return (
              <li key={r.panel ?? ""} className={findingStyles.item}>
                <StatusIcon level={framingLevel(v) ?? "info"} />
                <div className={findingStyles.body}>
                  <div className={findingStyles.title}>
                    {r.panel ? `Panel ${r.panel} · ` : ""}
                    {framingText(v)}
                  </div>
                  {framingDetails(v, data.minTolerancePx).map((d) => (
                    <p key={d} className={findingStyles.text}>
                      {d}
                    </p>
                  ))}
                  {v.status === "ready" ? (
                    <p className={findingStyles.text}>
                      Frame by frame, against {v.previous} with this night&apos;s dither added (up to{" "}
                      {Math.round(v.frameTolerancePx ?? 0)} px): {counts.ok} aligned · {counts.warn} a little off · {counts.bad} off
                      {outliers ? ` (${outliers} framed differently from the rest of the night)` : ""}. See the Framing column under Frames.
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {job && job.status !== "failed" ? (
        <p className={styles.meta}>
          Measuring the night&apos;s stars: {job.done}/{job.total}…
        </p>
      ) : null}
      {job?.status === "failed" ? <p className={styles.meta}>Measuring failed: {job.error}</p> : null}
    </section>
  );
}
