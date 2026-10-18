"use client";

import Link from "next/link";
import { useState } from "react";

import { ago, clock, enginePost, type GuardState, type LiveState } from "@/lib/live";
import { StatusIcon } from "../ui";
import styles from "./live.module.css";
import { useAdvanced } from "./useLive";

/** Connections, section tabs and the Advanced switch, shared by Live › Overview and Live › Guiding. */
export function LiveBar({ s, offline, tab }: { s: LiveState | null; offline: boolean; tab: "overview" | "guiding" }) {
  const [adv, setAdv] = useAdvanced();
  return (
    <div className={styles.bar}>
      <nav className={styles.tabs} aria-label="Live sections">
        <Link href="/live" className={tab === "overview" ? `${styles.tab} ${styles.tabOn}` : styles.tab} aria-current={tab === "overview" ? "page" : undefined}>
          Overview
        </Link>
        <Link href="/live/guiding" className={tab === "guiding" ? `${styles.tab} ${styles.tabOn}` : styles.tab} aria-current={tab === "guiding" ? "page" : undefined}>
          Guiding
        </Link>
      </nav>
      <div className={styles.conns}>
        {offline ? (
          <span className={styles.conn}>
            <StatusIcon level="bad" size={13} label="Engine offline" /> Engine not answering
          </span>
        ) : s ? (
          <>
            <Conn ok={s.nina.connected} name="NINA" detail={s.nina.connected ? (s.nina.websocket ? s.nina.url ?? "" : "events not connected") : s.nina.error ?? ""} where={s.nina.url} />
            <Conn ok={s.phd2.connected} name="PHD2" detail={s.phd2.connected ? s.phd2.address : s.phd2.error ?? ""} where={s.phd2.address} />
            {adv && s.nina.connected && Math.abs(s.nina.clockOffset) >= 2 ? (
              <span className={styles.conn} title="NINA's timestamps are corrected for this. Windows time sync on the observatory PC would remove it.">
                <StatusIcon level="info" size={13} /> Observatory PC clock {s.nina.clockOffset > 0 ? "+" : "−"}
                {Math.abs(s.nina.clockOffset).toFixed(0)} s vs this PC
              </span>
            ) : null}
            {s.simulated ? <span className={styles.sim}>Simulator</span> : null}
          </>
        ) : null}
      </div>
      <label className={styles.advToggle}>
        <input type="checkbox" checked={adv} onChange={(e) => setAdv(e.target.checked)} />
        <span>Advanced</span>
      </label>
      {s?.lost.length ? (
        <div className={styles.lostRow} role="status">
          {s.lost.map((d) => (
            <span key={d.key} className={styles.lost}>
              <StatusIcon level="warn" size={13} /> {d.label}
              {d.name ? ` (${d.name})` : ""} disconnected {ago(d.since)}
            </span>
          ))}
        </div>
      ) : null}
      {s?.guard ? <GuardRow g={s.guard} now={s.serverTime} /> : null}
    </div>
  );
}

/** The sequence guard, when it has something to say: a star being lost, a stopped sequence waiting to resume, or what it just did. */
function GuardRow({ g, now }: { g: GuardState; now: number }) {
  const [error, setError] = useState<string | null>(null);
  const last = g.log[0];
  const cancel = async () => {
    setError(null);
    try {
      await enginePost("/api/live/guard/cancel");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  let body: React.ReactNode = null;
  if (g.state === "paused" && g.pausedAt) {
    body = (
      <>
        <StatusIcon level="warn" size={13} /> Sequence stopped at {clock(g.pausedAt)}: guide star lost. It resumes after {g.resumeAfterS} s of steady guiding
        {g.steadyForS !== null ? ` (steady for ${g.steadyForS} s)` : " (PHD2 has not found the star yet)"}; gives up at {clock(g.giveUpAt)}.{" "}
        <button type="button" className={styles.link} onClick={() => void cancel()}>
          Don&apos;t resume
        </button>
      </>
    );
  } else if (g.state === "lost") {
    body = (
      <>
        <StatusIcon level="warn" size={13} /> Guide star lost {g.lostS} s of the last {g.stopAfterS} s ({g.lostPct}%): the sequence stops at {g.stopLostPct}%.
      </>
    );
  } else if (last && now - last.t < 900) {
    body = (
      <>
        <StatusIcon level="info" size={13} /> {clock(last.t)} · {last.message}
      </>
    );
  }
  if (!body && !g.error && !error) return null;
  return (
    <div className={styles.lostRow} role="status">
      {body ? <span className={styles.lost}>{body}</span> : null}
      {g.error || error ? (
        <span className={styles.lost}>
          <StatusIcon level="bad" size={13} /> {error ?? g.error}
        </span>
      ) : null}
    </div>
  );
}

function Conn({ ok, name, detail, where }: { ok: boolean; name: string; detail: string; where: string | null }) {
  return (
    <span className={styles.conn} title={detail || undefined}>
      <StatusIcon level={ok ? "ok" : "bad"} size={13} label={ok ? "Connected" : "Not connected"} /> {name}
      <span className={styles.connState}>{ok ? "connected" : `not connected${where ? ` · ${where}` : ""}`}</span>
    </span>
  );
}

/** Shown in place of a section when its source is not connected: what is wrong and where to fix it. */
export function NotConnected({ what, error, where }: { what: "NINA" | "PHD2"; error: string | null | undefined; where: string | null | undefined }) {
  const how =
    what === "NINA"
      ? "NINA needs the Advanced API plugin (Plugins → Available → Advanced API) with its server enabled, on port 1888 by default."
      : "In PHD2, switch on Tools → Enable Server (port 4400 for the first instance).";
  return (
    <div className={styles.empty}>
      <StatusIcon level="bad" size={15} />
      <div>
        <strong>
          {what} is not connected{where ? ` (${where})` : ""}.
        </strong>
        <p className={styles.hint}>
          {how} The address is in <Link href="/settings?tab=live">Settings → Live · Ingest</Link>. Retrying every few seconds.
          {error ? <span className="mono"> Last error: {error}</span> : null}
        </p>
      </div>
    </div>
  );
}
