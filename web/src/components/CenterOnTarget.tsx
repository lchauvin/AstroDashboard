"use client";

import { useEffect, useRef, useState } from "react";
import { Crosshair } from "lucide-react";

import { CENTER_ACTIVE, decText, enginePost, raText, type CenterState, type Sequence } from "@/lib/live";
import type { SessionTarget } from "@/lib/types";
import styles from "./SessionMatrix.module.css";

const POLL_MS = 2000;

type Mode = "center" | "update";

/**
 * Center on it: the night's target coordinates (where NINA's sequence was told to point, before any dither).
 * One button per night (per panel) opens a menu. "Center now" slews and centres; while the sequence runs that means stopping it (after the exposure in progress)
 * and starting it again, so it asks first. "Update sequence" only rewrites the sequence's target coordinates.
 * Always a click, never automatic.
 */
export function CenterOnTarget({ revId, folder, targets }: { revId: string; folder: string; targets: SessionTarget[] }) {
  const [menu, setMenu] = useState<SessionTarget | null>(null);
  const [warn, setWarn] = useState<{ t: SessionTarget; on: string | null } | null>(null);
  const [job, setJob] = useState<CenterState | null>(null);
  const [mine, setMine] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0); // re-arms the poll after a failed fetch
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const active = !!job && CENTER_ACTIVE.includes(job.phase);

  useEffect(() => {
    if (!mine || !active) return;
    timer.current = setTimeout(async () => {
      try {
        const r = await enginePost<{ center: CenterState | null }>("/api/live/center", undefined, "GET");
        setError(null);
        setJob(r.center);
      } catch (e) {
        setError(`${e instanceof Error ? e.message : "engine unreachable"} (retrying)`);
      }
      setTick((n) => n + 1);
    }, POLL_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [mine, active, tick]);

  useEffect(() => {
    if (!warn) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setWarn(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [warn]);

  async function start(t: SessionTarget, mode: Mode) {
    setBusy(true);
    setError(null);
    try {
      const r = await enginePost<{ center: CenterState }>("/api/live/center", { revision: revId, session: folder, panel: t.panel, waitFrame: true, mode });
      setJob(r.center);
      setMine(true);
      setWarn(null);
      setMenu(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  /** Center now: straight away, unless NINA's sequence is running (then the warning first). */
  async function centerNow(t: SessionTarget) {
    setBusy(true);
    setError(null);
    try {
      const live = await enginePost<{ sequence?: Sequence }>("/api/live/state", undefined, "GET");
      if (live.sequence?.status === "running") {
        setWarn({ t, on: live.sequence.target });
        setBusy(false);
        return;
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "engine unreachable");
      setBusy(false);
      return;
    }
    await start(t, "center");
  }

  async function cancel() {
    try {
      const r = await enginePost<{ center: CenterState | null }>("/api/live/center/cancel");
      setJob(r.center);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  if (!targets.length) return null;
  const multi = targets.length > 1;
  const label = (t: SessionTarget) => (multi && t.panel ? `Center on panel ${t.panel}` : "Center on it");
  const coords = (t: SessionTarget) => `RA ${raText(t.ra)} · Dec ${decText(t.dec)}${t.rotation != null ? ` · ${t.rotation.toFixed(1)}°` : ""}`;
  const title = (t: SessionTarget) =>
    `NINA's target this night${t.object ? ` (${t.object})` : ""}: ${coords(t)}, the centre before any dither.${
      t.variants > 1 ? ` The target was changed during the night (${t.variants} sets of coordinates): this is the first one.` : ""
    } Rotation is shown only: without a rotator, NINA centres the position.`;

  return (
    <div className={styles.center}>
      {targets.map((t) => (
        <button
          key={t.panel ?? ""}
          type="button"
          className={styles.ignore}
          disabled={busy || active}
          aria-expanded={menu === t}
          onClick={() => setMenu(menu === t ? null : t)}
          title={title(t)}
        >
          <Crosshair size={12} strokeWidth={1.8} aria-hidden="true" />
          {label(t)}
        </button>
      ))}
      {menu ? (
        <div className={styles.centerConfirm} role="dialog" aria-label={label(menu)}>
          <div className="mono">{coords(menu)}</div>
          <div className={styles.centerActions}>
            <button type="button" className={styles.centerGo} disabled={busy} onClick={() => void centerNow(menu)} title="Slew and centre on these coordinates">
              Center now
            </button>
            <button
              type="button"
              className={styles.centerGo}
              disabled={busy}
              onClick={() => void start(menu, "update")}
              title="Write these coordinates into NINA's sequence target (in NINA's memory; save the sequence in NINA to keep them)"
            >
              Update sequence
            </button>
            <button type="button" className={styles.ignore} onClick={() => setMenu(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
      {warn ? (
        <div className={styles.centerScrim} role="presentation" onClick={() => setWarn(null)}>
          <div className={styles.centerModal} role="alertdialog" aria-modal="true" aria-labelledby="center-warn-title" onClick={(e) => e.stopPropagation()}>
            <h3 id="center-warn-title">NINA&apos;s sequence is running</h3>
            <div className="mono">
              {warn.t.object ?? "Target"}
              {multi && warn.t.panel ? ` · panel ${warn.t.panel}` : ""} · {coords(warn.t)}
            </div>
            <p>
              {warn.on ? <>NINA is imaging {warn.on}. </> : null}Centering now waits for the exposure in progress to be saved, then stops the
              sequence, updates its target, slews and centres here, and once guiding has settled starts the sequence again. If anything fails
              after the stop, the sequence stays stopped.
            </p>
            <div className={styles.centerActions}>
              <button type="button" className={styles.centerGo} disabled={busy} onClick={() => void start(warn.t, "center")} autoFocus>
                Continue
              </button>
              <button type="button" className={styles.ignore} onClick={() => setWarn(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {mine && job ? (
        <div className={job.phase === "failed" ? styles.ignoreError : styles.centerStatus} role="status">
          {job.message}
          {job.phase === "waiting-frame" ? (
            <>
              {" "}
              <button type="button" className={styles.ignore} onClick={() => void cancel()}>
                Cancel
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      {error ? <span className={styles.ignoreError}>{error}</span> : null}
    </div>
  );
}
