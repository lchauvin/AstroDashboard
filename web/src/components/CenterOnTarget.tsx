"use client";

import { useEffect, useRef, useState } from "react";
import { Crosshair } from "lucide-react";

import { CENTER_ACTIVE, decText, enginePost, raText, type CenterState } from "@/lib/live";
import type { SessionTarget } from "@/lib/types";
import styles from "./SessionMatrix.module.css";

const POLL_MS = 2000;

/**
 * Center on it: send NINA back to the night's target coordinates (where its sequence was told to point, before any dither).
 * Running on that target: wait for the exposure in progress, stop the sequence, slew and centre, guiding back, start it again.
 * Loaded but not running: rewrite its target's coordinates. Always a click and a confirmation, never automatic.
 */
export function CenterOnTarget({ revId, folder, targets }: { revId: string; folder: string; targets: SessionTarget[] }) {
  const [confirm, setConfirm] = useState<SessionTarget | null>(null);
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

  async function start(t: SessionTarget) {
    setBusy(true);
    setError(null);
    try {
      const r = await enginePost<{ center: CenterState }>("/api/live/center", { revision: revId, session: folder, panel: t.panel, waitFrame: true });
      setJob(r.center);
      setMine(true);
      setConfirm(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
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
  const label = (t: SessionTarget) => (targets.length > 1 && t.panel ? `Center on panel ${t.panel}` : "Center on it");
  const coords = (t: SessionTarget) => `RA ${raText(t.ra)} · Dec ${decText(t.dec)}${t.rotation != null ? ` · ${t.rotation.toFixed(1)}°` : ""}`;

  return (
    <div className={styles.center}>
      {targets.map((t) => (
        <button
          key={t.panel ?? ""}
          type="button"
          className={styles.ignore}
          disabled={busy || active}
          onClick={() => setConfirm(confirm === t ? null : t)}
          title={`NINA's target this night${t.object ? ` (${t.object})` : ""}: ${coords(t)}, the centre before any dither.${
            t.variants > 1 ? ` The target was changed during the night (${t.variants} sets of coordinates): this is the first one.` : ""
          } Rotation is shown only: without a rotator, NINA centres the position.`}
        >
          <Crosshair size={12} strokeWidth={1.8} aria-hidden="true" />
          {label(t)}
        </button>
      ))}
      {confirm ? (
        <div className={styles.centerConfirm} role="dialog" aria-label="Center on it">
          <div className="mono">{coords(confirm)}</div>
          <p>
            If NINA&apos;s sequence is running on {confirm.object ?? "this target"}: after the exposure in progress is saved, it is stopped, the
            mount slews and centres here, and once guiding has settled the sequence starts again. If the sequence is loaded but not running, only
            its target&apos;s coordinates are updated (in NINA&apos;s memory; save the sequence in NINA to keep them).
          </p>
          <div className={styles.centerActions}>
            <button type="button" className={styles.centerGo} disabled={busy} onClick={() => void start(confirm)}>
              Center
            </button>
            <button type="button" className={styles.ignore} onClick={() => setConfirm(null)}>
              Cancel
            </button>
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
