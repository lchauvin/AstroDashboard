"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type { IngestStatus, SkyServer } from "@/lib/live";
import type { AdvisorSettings, ObservatorySettings, ReasoningEffort, SkyMonitorSettings } from "@/lib/planning/types";
import styles from "./settings.module.css";

export type LiveConn = {
  nina: boolean;
  ninaError: string | null;
  phd2: boolean;
  phd2Error: string | null;
  simulated: boolean;
  key: { ok: boolean; message: string };
  /** The sky monitor's Alpaca server, and how long ago NINA last read it ("" when it has not). */
  sky: { server: SkyServer; ninaRead: string } | null;
};

async function send(path: string, method: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

/** Settings → Live · Ingest: where NINA and PHD2 run, the AI advisor's model, and the NINA object → target table. */
export function LiveIngestSettings({ observatory, advisor, skyMonitor, conn, ingest }: { observatory: ObservatorySettings; advisor: AdvisorSettings; skyMonitor: SkyMonitorSettings; conn: LiveConn | null; ingest: IngestStatus | null }) {
  const router = useRouter();
  const [obs, setObs] = useState(observatory);
  const [adv, setAdv] = useState(advisor);
  const [sky, setSky] = useState(skyMonitor);
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

      <h3 className="eyebrow" style={{ marginTop: 28 }}>Tell NINA when it is cloudy</h3>
      <p className={styles.hint} style={{ marginTop: 6 }}>
        The dashboard is a safety monitor NINA connects to, “AstroDashboard sky monitor” (an ASCOM Alpaca device on port {conn?.sky?.server.port ?? 3008} of this PC).
        It says <b>unsafe</b> while PHD2&apos;s guide star has been lost for at least <b>Unsafe at</b> of the last <b>Window</b>, or its brightness
        there is under <b>Dim below</b> of what it was in clear sky: thin cloud and haze dim the star long before PHD2 loses it. It says
        <b> clear</b> again once PHD2 has guided <b>Clear after</b> seconds without losing the star, back at {Math.min(95, sky.dimPct + 15)}% of its
        brightness or more. It never stops, starts or changes anything in NINA: your sequence decides what unsafe means.
      </p>
      <div className={styles.rowActions} style={{ marginTop: 10 }}>
        <label className={styles.field} style={{ width: 120 }}>
          <span>Window (s)</span>
          <input className={styles.input} type="number" min={15} max={1800} step={15} value={sky.windowS} onChange={(e) => setSky({ ...sky, windowS: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 140 }}>
          <span>Unsafe at (% lost)</span>
          <input className={styles.input} type="number" min={50} max={100} step={5} value={sky.lostPct} onChange={(e) => setSky({ ...sky, lostPct: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 150 }}>
          <span>Dim below (% of clear)</span>
          <input className={styles.input} type="number" min={10} max={90} step={5} value={sky.dimPct} onChange={(e) => setSky({ ...sky, dimPct: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 130 }}>
          <span>Clear after (s)</span>
          <input className={styles.input} type="number" min={15} max={1800} step={15} value={sky.clearAfterS} onChange={(e) => setSky({ ...sky, clearAfterS: Number(e.target.value) })} />
        </label>
        <label className={styles.field} style={{ width: 150 }}>
          <span>Give up after (min)</span>
          <input className={styles.input} type="number" min={5} max={720} step={5} value={sky.giveUpMin} onChange={(e) => setSky({ ...sky, giveUpMin: Number(e.target.value) })} />
        </label>
        <button
          type="button"
          className="button button-primary"
          onClick={() =>
            run(() => {
              const { enabled: _enabled, ...thresholds } = sky;
              return send("/api/settings", "PUT", { skyMonitor: thresholds });
            }, "Saved.")
          }
        >
          Save
        </button>
      </div>
      <p className={styles.hint}>
        Never unsafe: while PHD2 is not guiding (a flip, an autofocus, a slew: no evidence), while the mount points below the horizon profile of the
        site (<Link href="/settings?tab=sites">Settings › Sites</Link>; a building is not cloud), and after <b>Give up after</b> minutes unsafe without a
        break (more likely an obstruction the profile lacks). The last two say clear until the next guiding run, as does <b>Report clear</b> on the
        Live bar. Turn it on or off on <Link href="/live">Live › Overview</Link> (Sequence card, off by default{skyMonitor.enabled ? "; on now" : ""}).
      </p>
      <p className={styles.hint}>
        {conn?.sky
          ? conn.sky.server.error
            ? `Not served to NINA: ${conn.sky.server.error}.`
            : conn.sky.server.running
              ? `Served on port ${conn.sky.server.port}${conn.sky.server.discovery ? ", with Alpaca discovery" : " (no discovery: add it in NINA by address)"}; ${conn.sky.ninaRead ? `NINA last read it ${conn.sky.ninaRead}` : "NINA has not connected to it yet"}.`
              : "Not served to NINA (the engine runs without it)."
          : null}
      </p>
      <p className={styles.hint} style={{ marginBottom: 4 }}>
        <b>In NINA</b>
      </p>
      <ol className={styles.hint} style={{ marginTop: 0, paddingLeft: 20 }}>
        <li>
          Equipment › Safety Monitor: pick <b>AstroDashboard sky monitor</b> (Alpaca discovery finds it; rescan the device list) and connect. Not
          listed? Let Python through this PC&apos;s firewall (TCP {conn?.sky?.server.port ?? 3008}, UDP 32227).
        </li>
        <li>
          In the target&apos;s imaging loop (the container with <b>Loop Until Time</b> or altitude), put the exposures and their Dither in a
          Sequential Instruction Set with the condition <b>Loop While Safe</b>: NINA interrupts the exposure when unsafe and does not save it.
        </li>
        <li>
          Below it, in the same loop, a second Sequential Instruction Set with the condition <b>Loop While Unsafe</b>, holding one <b>Wait for
          Time Span</b> of 1 min. Short waits rather than one Wait until Safe: NINA checks triggers between instructions only, so the meridian
          flip still gets its chance every minute through a long cloudy spell.
        </li>
        <li>
          Keep PHD2 guiding meanwhile: its star is how the dashboard sees the sky clear. <b>Center After Drift</b> and <b>Restore Guiding</b> on
          the imaging loop re-center and restart guiding after a cloud. Stop guiding when the target ends, so its evidence does not carry over to
          the next one. The loop&apos;s own time or altitude condition ends a wait that lasts too long, and the end of the sequence runs as usual.
        </li>
      </ol>
      <pre className={`${styles.hint} mono`} style={{ marginTop: 4, overflowX: "auto" }}>
        {[
          "Imaging            ⟲ Loop Until Time    ⚡ Restore Guiding, Center After Drift",
          "├─ Clear sky            ⟲ Loop While Safe",
          "│  ├─ Smart Exposure …  (each filter)",
          "│  └─ Dither",
          "└─ Wait for clear sky   ⟲ Loop While Unsafe",
          "   └─ Wait for Time Span  1 min",
        ].join("\n")}
      </pre>

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
