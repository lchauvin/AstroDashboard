"use client";

import Link from "next/link";
import { useState } from "react";

import { ago, clock, enginePost, type LiveState, type SkyState } from "@/lib/live";
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
      {s?.sky ? <SkyRow k={s.sky} now={s.serverTime} /> : null}
    </div>
  );
}

/** The sky monitor, when it has something to say: clouds (NINA told unsafe), a star being lost or dimmed, an obstruction, or what it just did. */
function SkyRow({ k, now }: { k: SkyState; now: number }) {
  const [error, setError] = useState<string | null>(null);
  const last = k.log[0];
  const clearNow = async () => {
    setError(null);
    try {
      await enginePost("/api/live/sky/clear");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const dimmed = k.brightnessPct !== null && k.brightnessPct < k.clearNeedPct;
  let body: React.ReactNode = null;
  if (k.state === "cloudy" && k.cloudySince) {
    const steady = Math.min(k.steadyForS ?? 0, k.clearAfterS);
    body = (
      <>
        <span title={`Gives up at ${clock(k.giveUpAt)}: unsafe for ${k.giveUpMin} min without a break is more likely an obstruction, so NINA is then told clear until the next guiding run.`}>
          <StatusIcon level="warn" size={13} /> Cloudy since {clock(k.cloudySince)}: {k.reason}. NINA is told unsafe. Clear after {k.clearAfterS} s steady
          {k.baselineKnown ? ` at ${k.clearNeedPct}% brightness or more` : ""}: {steady}/{k.clearAfterS} s
          {k.brightnessPct !== null ? `, star at ${k.brightnessPct}%` : ""}.
        </span>{" "}
        <button type="button" className={styles.link} title="Tell NINA it is clear until the next guiding run: an obstruction, or you know better." onClick={() => void clearNow()}>
          Report clear
        </button>
      </>
    );
  } else if (k.state === "watching" && (k.lostS > 0 || dimmed)) {
    const parts = [k.lostS > 0 ? `lost ${k.lostS} s of the last ${k.windowS} s (${k.lostSharePct}%)` : null, dimmed ? `at ${k.brightnessPct}% of its clear-sky brightness` : null];
    body = (
      <>
        <StatusIcon level="warn" size={13} /> Guide star {parts.filter(Boolean).join(", ")}: unsafe at {k.lostPct}% lost or under {k.dimPct}%.
      </>
    );
  } else if (k.state === "obstructed" && k.obstructed) {
    body = (
      <>
        <StatusIcon level="info" size={13} /> {k.obstructed}: not cloud, NINA is told clear.
      </>
    );
  } else if (last && now - last.t < 900) {
    body = (
      <>
        <StatusIcon level="info" size={13} /> {clock(last.t)} · {last.message}
      </>
    );
  }
  const serverError = k.enabled ? k.server.error : null;
  if (!body && !serverError && !error) return null;
  return (
    <div className={styles.lostRow} role="status">
      {body ? <span className={styles.lost}>{body}</span> : null}
      {serverError || error ? (
        <span className={styles.lost}>
          <StatusIcon level="bad" size={13} /> {error ?? `Sky monitor not served to NINA: ${serverError}`}
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
