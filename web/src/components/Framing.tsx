"use client";

import { createContext, useContext, useEffect, useState } from "react";

import { framingBusy, framingDetails, framingLevel, framingText, px, type FramingVerdict, type RevisionFraming } from "@/lib/framing";
import { StatusIcon, Tip } from "./ui";
import styles from "./SessionMatrix.module.css";

type State = { data: RevisionFraming | null; error: string | null };
const FramingContext = createContext<State>({ data: null, error: null });

/** Fetches a revision's framing for the session matrix, and asks again while the engine measures stars. */
export function FramingProvider({ revId, children }: { revId: string; children: React.ReactNode }) {
  const [state, setState] = useState<State>({ data: null, error: null });
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const res = await fetch(`/engine/api/framing/revisions/${encodeURIComponent(revId)}`, { cache: "no-store" });
        if (!res.ok) throw new Error(res.status === 503 ? "the first scan is still running" : `engine error ${res.status}`);
        const data = (await res.json()) as RevisionFraming;
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
  }, [revId]);
  return <FramingContext.Provider value={state}>{children}</FramingContext.Provider>;
}

function Mark({ v, panel, minTolerancePx }: { v: FramingVerdict; panel: string; minTolerancePx: number }) {
  const level = framingLevel(v);
  const details = framingDetails(v, minTolerancePx);
  return (
    <Tip
      block
      side="left"
      tip={
        <>
          <strong>Framing{panel ? ` · panel ${panel}` : ""}</strong>
          <br />
          {framingText(v)}
          {details.map((d) => (
            <span key={d}>
              <br />
              <span className="num">{d}</span>
            </span>
          ))}
        </>
      }
    >
      <span className={styles.framingMark}>
        {panel ? <span className={styles.framingPanel}>P{panel}</span> : null}
        {level ? (
          <StatusIcon level={level} size={13} label={framingText(v)} />
        ) : (
          <span className={styles.framingNote}>{v.status === "first" ? "ref" : v.status === "pending" ? "…" : "?"}</span>
        )}
        {v.status === "ready" ? <span className="num">{px(v.cornerPx)}</span> : null}
      </span>
    </Tip>
  );
}

/** One night's framing against the previous night (one mark per mosaic panel). */
export function FramingCell({ folder }: { folder: string }) {
  const { data, error } = useContext(FramingContext);
  const byPanel = data?.sessions[folder];
  if (!byPanel) {
    return (
      <td className={`${styles.framing} muted`} aria-label={data ? "No lights" : error ? "Framing unavailable" : "Loading"}>
        {data || error ? "—" : "…"}
      </td>
    );
  }
  const keys = Object.keys(byPanel).sort((a, b) => Number(a) - Number(b) || a.localeCompare(b));
  return (
    <td className={styles.framing}>
      {keys.map((p) => (
        <Mark key={p} v={byPanel[p]} panel={p} minTolerancePx={data.minTolerancePx} />
      ))}
    </td>
  );
}

/** The column's key, and the measuring progress while stars are being measured. */
export function FramingKey() {
  const { data, error } = useContext(FramingContext);
  const job = data?.job;
  return (
    <p className={styles.key}>
      Framing vs the previous night (shift and rotation, at the corners): <StatusIcon level="ok" size={12} /> within the dither ·{" "}
      <StatusIcon level="warn" size={12} /> up to twice it · <StatusIcon level="bad" size={12} /> beyond; ref = first night.
      {job && job.status !== "failed" ? (
        <span>
          {" "}
          Measuring stars {job.done}/{job.total}…
        </span>
      ) : null}
      {job?.status === "failed" ? <span> Measuring failed: {job.error}</span> : null}
      {error && !data ? <span> Framing unavailable: {error}</span> : null}
    </p>
  );
}
