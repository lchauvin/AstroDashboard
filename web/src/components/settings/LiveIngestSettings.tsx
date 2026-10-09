"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { IngestStatus } from "@/lib/live";
import styles from "./settings.module.css";

async function send(path: string, method: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

/** Settings → Data · Ingest · Live: AstroController address and the NINA object → target table. */
export function LiveIngestSettings({ liveUrl, tokenSet, online, ingest }: { liveUrl: string; tokenSet: boolean; online: boolean; ingest: IngestStatus | null }) {
  const router = useRouter();
  const [url, setUrl] = useState(liveUrl);
  const [object, setObject] = useState("");
  const [rev, setRev] = useState(ingest?.targets.find((t) => t.stage === "acquiring")?.id ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null);
    try {
      await fn();
      setMsg(ok);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const revName = (id: string | null) => ingest?.targets.find((t) => t.id === id)?.folder ?? id;

  return (
    <div className={styles.rig}>
      <h3 className="eyebrow">AstroController (Live)</h3>
      <div className={styles.rowActions} style={{ marginTop: 8 }}>
        <label className={styles.field} style={{ minWidth: 320 }}>
          <span>Address</span>
          <input className={styles.input} value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <button type="button" className="button button-primary" onClick={() => run(() => send("/api/settings", "PUT", { liveUrl: url.trim() }), "Saved.")}>
          Save
        </button>
      </div>
      <p className={styles.hint}>
        {online ? "Reachable now." : "Not reachable right now."} Token: {tokenSet ? "set for the engine (ASTROCONTROLLER_TOKEN)" : "none — only needed if AstroController runs with one"}. The token is read from
        the engine&apos;s environment, never stored or shown here.
      </p>

      <h3 className="eyebrow" style={{ marginTop: 28 }}>NINA object → target</h3>
      <p className={styles.hint}>
        Auto-ingest sends frames by their OBJECT header. Rows marked “learned” come from the frames already in your Library;
        your own rows win. Frames go to the target&apos;s latest acquiring revision unless a row names a revision.
      </p>
      <table className="table" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>NINA object</th>
            <th>Goes to</th>
            <th>Source</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {(ingest?.objectMap ?? []).map((m) => (
            <tr key={m.object}>
              <td>{m.object}</td>
              <td>{m.revisionId ? revName(m.revisionId) : m.catalogId}</td>
              <td className={styles.hint}>{m.learned ? `learned · ${m.frames ?? 0} frames` : "yours"}</td>
              <td>
                {!m.learned ? (
                  <button type="button" className={styles.link} onClick={() => run(() => send(`/api/ingest/map?object=${encodeURIComponent(m.object)}`, "DELETE"), `Removed “${m.object}”.`)}>
                    Remove row
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.rowActions} style={{ marginTop: 12 }}>
        <label className={styles.field}>
          <span>NINA object name</span>
          <input className={styles.input} value={object} onChange={(e) => setObject(e.target.value)} placeholder="Soul Nebula" />
        </label>
        <label className={styles.field}>
          <span>Target revision</span>
          <select className={styles.input} value={rev} onChange={(e) => setRev(e.target.value)}>
            {(ingest?.targets ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.folder} — {t.stage}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button"
          disabled={!object.trim() || !rev}
          onClick={() => run(async () => { await send("/api/ingest/map", "PUT", { object: object.trim(), revisionId: rev }); setObject(""); }, "Mapping saved.")}
        >
          Add mapping
        </button>
      </div>
      {msg ? <p className={styles.ok}>{msg}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      <p className={styles.hint} style={{ marginTop: 18 }}>
        The ingest mode (Off / Dry-run / Auto) and each night&apos;s plan, undo and cleanup are on the Live page. Data root and
        filter aliases are read from <code>astrodashboard.toml</code>.
      </p>
    </div>
  );
}
