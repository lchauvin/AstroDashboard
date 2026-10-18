"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type { IngestStatus } from "@/lib/live";
import type { AdvisorSettings, GuardSettings, ObservatorySettings, ReasoningEffort } from "@/lib/planning/types";
import styles from "./settings.module.css";

export type LiveConn = { nina: boolean; ninaError: string | null; phd2: boolean; phd2Error: string | null; simulated: boolean; key: { ok: boolean; message: string } };

async function send(path: string, method: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

/** Settings → Live · Ingest: where NINA and PHD2 run, the AI advisor's model, and the NINA object → target table. */
export function LiveIngestSettings({ observatory, advisor, guard, conn, ingest }: { observatory: ObservatorySettings; advisor: AdvisorSettings; guard: GuardSettings; conn: LiveConn | null; ingest: IngestStatus | null }) {
  const router = useRouter();
  const [obs, setObs] = useState(observatory);
  const [adv, setAdv] = useState(advisor);
  const [grd, setGrd] = useState(guard);
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
      <h3 className="eyebrow">Observatory (Live)</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        The engine connects to NINA and PHD2 itself. In NINA, install the <b>Advanced API</b> plugin and enable its server; in PHD2, switch on{" "}
        <b>Tools → Enable Server</b>. Neither has a password, so keep them on your local network.
      </p>
      <div className={styles.rowActions} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ minWidth: 200 }}>
          <span>NINA host</span>
          <input className={styles.input} value={obs.ninaHost} onChange={(e) => setObs({ ...obs, ninaHost: e.target.value })} placeholder="192.168.1.20" />
        </label>
        <label className={styles.field} style={{ width: 110 }}>
          <span>Port</span>
          <input className={styles.input} type="number" value={obs.ninaPort} onChange={(e) => setObs({ ...obs, ninaPort: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ minWidth: 200 }}>
          <span>PHD2 host</span>
          <input className={styles.input} value={obs.phd2Host} onChange={(e) => setObs({ ...obs, phd2Host: e.target.value })} placeholder="192.168.1.20" />
        </label>
        <label className={styles.field} style={{ width: 110 }}>
          <span>Instance</span>
          <input className={styles.input} type="number" min={1} max={9} value={obs.phd2Instance} onChange={(e) => setObs({ ...obs, phd2Instance: Number(e.target.value) })} />
        </label>
        <button type="button" className="button button-primary" onClick={() => run(() => send("/api/settings", "PUT", { observatory: obs }), "Saved — reconnecting.")}>
          Save
        </button>
      </div>
      <p className={styles.hint}>
        {conn?.simulated
          ? "The engine runs with --fake-observatory: these addresses are saved but the simulator is used."
          : conn
            ? `NINA: ${conn.nina ? "connected" : `not connected${conn.ninaError ? ` (${conn.ninaError})` : ""}`} · PHD2: ${conn.phd2 ? "connected" : `not connected${conn.phd2Error ? ` (${conn.phd2Error})` : ""}`}.`
            : "Connection state unknown (engine offline)."}{" "}
        PHD2 listens on port 4400 + instance − 1.
      </p>

      <h3 className="eyebrow" style={{ marginTop: 28 }}>AI guiding advisor</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        Model as <span className="mono">provider/model-id</span>: <span className="mono">ollama/llama3.1:8b</span> (local, no key),{" "}
        <span className="mono">ollama_cloud/…</span> or <span className="mono">ollama/…</span> with the URL{" "}
        <span className="mono">https://ollama.com</span> (OLLAMA_API_KEY), <span className="mono">openrouter/…</span> (OPENROUTER_API_KEY),{" "}
        <span className="mono">openai/…</span> (OPENAI_API_KEY) or <span className="mono">anthropic/claude-sonnet-5-5</span> (ANTHROPIC_API_KEY). Keys go in{" "}
        <span className="mono">.env</span> next to astrodashboard.toml and are never shown or stored here. Turn the advisor on or off on the Guiding page.
        Reasoning <b>none</b> answers in seconds within a 3000-token cap; <b>low</b>–<b>high</b> let a thinking model reason with no token
        cap (up to 10 min a call). Anthropic models ignore it.
      </p>
      <div className={styles.rowActions} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ minWidth: 320 }}>
          <span>Model</span>
          <input className={styles.input} value={adv.model} onChange={(e) => setAdv({ ...adv, model: e.target.value })} />
        </label>
        <label className={styles.field} style={{ width: 130 }}>
          <span>Reasoning</span>
          <select className={styles.input} value={adv.effort} onChange={(e) => setAdv({ ...adv, effort: e.target.value as ReasoningEffort })}>
            <option value="none">None</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label className={styles.field} style={{ width: 130 }}>
          <span>Every (min)</span>
          <input className={styles.input} type="number" min={2} max={120} value={adv.intervalMin} onChange={(e) => setAdv({ ...adv, intervalMin: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 150 }}>
          <span>Target RMS (″)</span>
          <input className={styles.input} type="number" step={0.05} min={0.2} max={5} value={adv.targetRms} onChange={(e) => setAdv({ ...adv, targetRms: Number(e.target.value) })} />
        </label>
        {adv.model.startsWith("ollama/") ? (
          <label className={styles.field} style={{ minWidth: 240 }}>
            <span>Ollama URL</span>
            <input className={styles.input} value={adv.ollamaUrl} onChange={(e) => setAdv({ ...adv, ollamaUrl: e.target.value })} />
          </label>
        ) : null}
        <button
          type="button"
          className="button button-primary"
          onClick={() => run(() => send("/api/settings", "PUT", { advisor: { model: adv.model.trim(), effort: adv.effort, intervalMin: adv.intervalMin, targetRms: adv.targetRms, ollamaUrl: adv.ollamaUrl.trim() } }), "Saved.")}
        >
          Save
        </button>
      </div>
      <p className={styles.hint}>
        {conn ? (conn.key.ok ? `Key: ${conn.key.message}.` : `${conn.key.message}.`) : null} The target RMS also sets the green/amber levels on the
        Guiding page; the automatic check does not ask the model while both axes are within it.
      </p>

      <h3 className="eyebrow" style={{ marginTop: 28 }}>Pause the sequence when guiding is lost</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        When the guide star has been lost (clouds, a tree) for longer than <b>Stop after</b>, the dashboard stops the NINA sequence; once PHD2 has
        guided for <b>Resume after</b> without losing it, it starts the sequence again. NINA picks up at the first unfinished instruction and
        retakes the interrupted exposure. Its pre-sequence checklist (“… continue anyway?”, e.g. with the flat panel unplugged) is skipped on
        resume, so it cannot hold up the night. A deliberate guiding stop (meridian flip, autofocus, centering) never counts. PHD2 has to find
        its star again by itself: if the star drifted out of its search region meanwhile, guiding does not come back, and neither does the
        sequence.
      </p>
      <div className={styles.rowActions} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ width: 130 }}>
          <span>Stop after (s)</span>
          <input className={styles.input} type="number" min={15} max={1800} step={15} value={grd.stopAfterS} onChange={(e) => setGrd({ ...grd, stopAfterS: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 140 }}>
          <span>Resume after (s)</span>
          <input className={styles.input} type="number" min={15} max={1800} step={15} value={grd.resumeAfterS} onChange={(e) => setGrd({ ...grd, resumeAfterS: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 150 }}>
          <span>Give up after (min)</span>
          <input className={styles.input} type="number" min={5} max={720} step={5} value={grd.maxWaitMin} onChange={(e) => setGrd({ ...grd, maxWaitMin: Number(e.target.value) })} />
        </label>
        <button type="button" className="button button-primary" onClick={() => run(() => send("/api/settings", "PUT", { guard: { stopAfterS: grd.stopAfterS, resumeAfterS: grd.resumeAfterS, maxWaitMin: grd.maxWaitMin } }), "Saved.")}>
          Save
        </button>
      </div>
      <p className={styles.hint}>
        Give up: if guiding has not been steady again within this time, the sequence stays stopped. Starting the sequence yourself meanwhile
        cancels the resume. Turn it on or off on <Link href="/live">Live › Overview</Link> (Sequence card, off by default{guard.enabled ? "; on now" : ""});
        its state shows at the top of the Live pages.
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
