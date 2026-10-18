"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { NotifySettings as Settings } from "@/lib/planning/types";
import styles from "./settings.module.css";

type Preview = { date: string; site: string; label: string; worthIt: boolean; reason: string; message: { title: string; message: string } | null };

async function call<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : typeof data?.error === "string" ? data.error : `Error ${res.status}`);
  return data as T;
}

/** A topic nobody will guess: ntfy topics are public to anyone who knows the name. */
const randomTopic = () => `astrodash-${Array.from(crypto.getRandomValues(new Uint8Array(9)), (b) => "abcdefghjkmnpqrstuvwxyz23456789"[b % 31]).join("")}`;

/** Settings → Notifications: tonight's Planning verdict pushed to a phone through ntfy. */
export function NotifySettings({ saved }: { saved: Settings }) {
  const router = useRouter();
  const [s, setS] = useState(saved);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    setMsg(null);
    try {
      await fn();
      setMsg(ok);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const save = (patch: Partial<Settings> = {}) => call("/engine/api/settings", "PUT", { notify: { ...s, ...patch } });
  const subscribeUrl = s.ntfyTopic ? `${s.ntfyServer.replace(/\/+$/, "")}/${s.ntfyTopic}` : null;

  return (
    <div className={styles.rig} style={{ maxWidth: 760 }}>
      <h3 className="eyebrow">Tonight on your phone (ntfy)</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        When the Planning page rates tonight <b>Excellent</b> or <b>Good</b> (and, if you like, <b>Clear · moonlit</b>), you get a notification
        with the clear window, the GFS/ICON agreement, the moon, seeing, transparency, dew and the best target. Install the free{" "}
        <b>ntfy</b> app (Android, iOS), tap <b>+</b> and subscribe to the topic below. No account is needed, but anyone who knows the topic can
        read it, so keep it hard to guess. A later check sends an update only if the verdict changes, including when tonight stops being worth it.
      </p>
      <div className={styles.rowFields} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ minWidth: 280 }}>
          <span>Topic</span>
          <input className={styles.input} value={s.ntfyTopic} onChange={(e) => setS({ ...s, ntfyTopic: e.target.value })} placeholder="astrodash-…" />
        </label>
        <button type="button" className="button" onClick={() => setS({ ...s, ntfyTopic: randomTopic() })}>
          Generate
        </button>
        <label className={styles.field} style={{ minWidth: 200 }}>
          <span>ntfy server</span>
          <input className={styles.input} value={s.ntfyServer} onChange={(e) => setS({ ...s, ntfyServer: e.target.value })} />
        </label>
      </div>
      <div className={styles.rowFields} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ minWidth: 280 }}>
          <span>Dashboard link (optional)</span>
          <input className={styles.input} value={s.dashboardUrl} onChange={(e) => setS({ ...s, dashboardUrl: e.target.value })} placeholder="http://192.168.2.10:3006" />
        </label>
        <label className={styles.switch}>
          <input type="checkbox" checked={s.moonlit} onChange={(e) => setS({ ...s, moonlit: e.target.checked })} />
          Also clear but moonlit nights
        </label>
        <label className={styles.switch}>
          <input type="checkbox" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />
          On
        </label>
        <button type="button" className="button button-primary" onClick={() => run(() => save(), "Saved.")}>
          Save
        </button>
      </div>
      <p className={styles.hint}>
        {subscribeUrl ? (
          <>
            Subscribe to <span className="mono">{s.ntfyTopic}</span> in the app{s.ntfyServer !== "https://ntfy.sh" ? ` (server ${s.ntfyServer})` : ""}, or open{" "}
            <a href={subscribeUrl} target="_blank" rel="noreferrer" className="mono">
              {subscribeUrl}
            </a>
            .{" "}
          </>
        ) : null}
        The dashboard link opens tonight&apos;s plan when you tap the notification; it has to be an address your phone can reach (this PC&apos;s
        LAN address, port 3006).
      </p>

      <div className={styles.rowActions} style={{ marginTop: 14 }}>
        <button type="button" className="button" onClick={() => run(async () => setPreview(await call<Preview>("/api/notify/tonight", "GET")), "")}>
          Preview tonight
        </button>
        <button type="button" className="button" disabled={!saved.ntfyTopic} onClick={() => run(() => call("/api/notify/tonight?test=1", "POST"), "Test sent: check your phone.")}>
          Send test
        </button>
        {msg ? <span className={styles.ok}>{msg}</span> : null}
        {error ? <span className={styles.error}>{error}</span> : null}
      </div>
      {preview?.message ? (
        <div className={styles.framePreview} style={{ marginTop: 12 }}>
          <b>{preview.message.title}</b>
          <span style={{ whiteSpace: "pre-line" }}>{preview.message.message}</span>
          <span className={styles.hint}>
            {preview.date} · {preview.site} · {preview.worthIt ? "would be sent" : "would not be sent (not good enough)"}
          </span>
        </div>
      ) : null}

      <h3 className="eyebrow" style={{ marginTop: 28 }}>When it checks</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        A Windows scheduled task asks the dashboard at 15:00 and 19:30 (install it once with{" "}
        <span className="mono">scripts\install-notify-task.ps1</span>). The engine and the web UI have to be running then, since the forecast
        comes through the engine; a missed check is logged in <span className="mono">var\notify.log</span>. Send test works without the task.
      </p>
    </div>
  );
}
