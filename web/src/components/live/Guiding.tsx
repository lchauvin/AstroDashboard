"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { ago, clock, duration, enginePost, fmt, type Analysis, type Change, type LiveState, type Suggestion } from "@/lib/live";
import { Marks, StatusIcon, Tip } from "../ui";
import { LiveBar, NotConnected } from "./LiveBar";
import styles from "./live.module.css";
import { Scatter } from "./Scatter";
import { Legend, TimeChart } from "./TimeChart";
import { useAdvanced, useLive } from "./useLive";

const RA = "var(--filter-B)";
const DEC = "var(--filter-H)";
const RANGES = [120, 300, 600, 1800];
const SCALES: (number | "auto")[] = ["auto", 1, 2, 4];

type Act = (fn: () => Promise<unknown>, ok?: string) => Promise<void>;

export function Guiding({ initial }: { initial: LiveState | null }) {
  const [range, setRange] = useState(300);
  const [scale, setScale] = useState<number | "auto">("auto");
  const { data: s, offline, refresh } = useLive(initial, range);
  const [adv] = useAdvanced();
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const act: Act = async (fn, ok) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg(ok);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!s) return <LiveBar s={null} offline tab="guiding" />;
  const g = s.guiding;
  const st = g.stats;
  const p = s.phd2;
  const scalePx = p.pixelScale ?? g.scale;
  const target = s.advisor.targetRms;
  const lvl = (v: number | undefined, t: number) => (v == null ? "info" : v <= t ? "ok" : v <= t * 1.5 ? "warn" : "bad");
  const axisT = target / Math.SQRT2;

  return (
    <div className={styles.live}>
      <LiveBar s={s} offline={offline} tab="guiding" />
      {!p.connected ? <NotConnected what="PHD2" error={p.error} where={p.address} /> : null}

      {p.connected ? (
        <div className={styles.statusLine}>
          <span className={styles.state}>
            <StatusIcon level={p.appState === "Guiding" ? "ok" : p.appState === "LostLock" ? "bad" : "warn"} size={14} /> {p.appState}
          </span>
          {p.guidingForS ? <span>for {duration(p.guidingForS)}</span> : null}
          {g.active.length ? <span className={styles.activity}>{g.active.join(" · ")}</span> : null}
          {p.lastAlert?.msg && p.lastAlert.at && Date.now() / 1000 - p.lastAlert.at < 600 ? (
            <span className={styles.flagged}>
              <StatusIcon level="warn" size={13} /> PHD2: {p.lastAlert.msg} ({ago(p.lastAlert.at)})
            </span>
          ) : null}
          {adv ? <Controls s={s} act={act} busy={busy} /> : null}
        </div>
      ) : null}

      <div className={styles.tiles}>
        <Tile label="Total RMS" value={fmt(st?.total, 2, "″")} level={lvl(st?.total, target)} sub={`target ${target.toFixed(2)}″${st && scalePx ? ` · ${(st.total / scalePx).toFixed(2)} px` : ""}`} />
        <Tile label="RA" value={fmt(st?.ra.rms, 2, "″")} level={lvl(st?.ra.rms, axisT)} sub={adv && st ? `peak ${st.ra.peak.toFixed(2)}″` : "right ascension"} />
        <Tile label="Dec" value={fmt(st?.dec.rms, 2, "″")} level={lvl(st?.dec.rms, axisT)} sub={adv && st ? `peak ${st.dec.peak.toFixed(2)}″` : "declination"} />
        <Tile label="Guide star" value={st ? `SNR ${st.snr.toFixed(0)}` : "—"} level={st ? (st.snr < 8 ? "warn" : "ok") : "info"} sub={st && scalePx ? `seeing ≈ ${(st.hfdPx * scalePx).toFixed(1)}″ (HFD)` : undefined} />
        {adv && st ? <Tile label="Sample" value={`${st.n} frames`} sub={`${duration(st.seconds)} clean · star mass ${st.mass}`} /> : null}
      </div>
      <p className={styles.hint}>
        Statistics over the last {duration(300)} of clean guiding — dithers, settling, star loss, flips and autofocus are left out. RMS is measured about zero, so a steady drift counts as error.
      </p>

      <div className={styles.guideGrid}>
        <section className={`framed ${styles.card}`}>
          <Marks />
          <div className={styles.cardHead}>
            <span className="eyebrow">Guide error · arcsec</span>
            <div className={styles.seg}>
              {RANGES.map((r) => (
                <button key={r} type="button" className={r === range ? styles.segOn : undefined} onClick={() => setRange(r)}>
                  {r / 60} min
                </button>
              ))}
              <span className={styles.segGap} />
              {SCALES.map((v) => (
                <button key={String(v)} type="button" className={v === scale ? styles.segOn : undefined} onClick={() => setScale(v)} title="Vertical scale">
                  {v === "auto" ? "auto" : `±${v}″`}
                </button>
              ))}
            </div>
          </div>
          <Legend series={[{ label: "RA", color: RA }, { label: "Dec", color: DEC }]} excluded events />
          {g.graph.t.length > 2 ? (
            <TimeChart
              t={g.graph.t}
              seconds={range}
              unit="″"
              limit={scale}
              height={250}
              excluded={g.graph.excluded}
              events={g.graph.events}
              series={[
                { key: "ra", label: "RA", color: RA, values: g.graph.ra },
                { key: "dec", label: "Dec", color: DEC, values: g.graph.dec },
              ]}
              extra={(i) => `Star SNR ${g.graph.snr[i]}`}
              ariaLabel={`RA and Dec guide error, last ${range / 60} minutes`}
            />
          ) : (
            <p className={styles.hint}>No guide steps yet.</p>
          )}
          {adv && g.graph.t.length > 2 ? (
            <>
              <div className="eyebrow" style={{ marginTop: 6 }}>
                Corrections · pulse length, ms (+ East / North)
              </div>
              <TimeChart
                t={g.graph.t}
                seconds={range}
                unit=" ms"
                digits={0}
                minLimit={100}
                height={130}
                series={[
                  { key: "raMs", label: "RA", color: RA, values: g.graph.raMs },
                  { key: "decMs", label: "Dec", color: DEC, values: g.graph.decMs },
                ]}
                ariaLabel="RA and Dec correction pulses"
              />
            </>
          ) : null}
        </section>
        <div className={styles.sideCol}>
          <GuideStar connected={p.connected} />
          <section className={`framed ${styles.card}`}>
            <Marks />
            <span className="eyebrow">Scatter · last {range / 60} min</span>
            <Scatter ra={g.graph.ra} dec={g.graph.dec} excluded={g.graph.excluded} ring={target / Math.SQRT2} />
            <p className={styles.hint}>Dashed ring: the per-axis target ({axisT.toFixed(2)}″). A round cloud is seeing; a stretched one points at an axis.</p>
          </section>
        </div>
      </div>

      {msg ? <p className={styles.ok}>{msg}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}

      <div className={styles.twoCol}>
        <DiagnosisCard s={s} adv={adv} />
        <AdvisorCard s={s} act={act} busy={busy} adv={adv} />
      </div>

      {p.connected ? <Params s={s} act={act} busy={busy} adv={adv} /> : null}
      <Changes changes={s.advisor.changes} act={act} busy={busy} connected={p.connected} adv={adv} />
    </div>
  );
}

function Tile({ label, value, sub, level }: { label: string; value: string; sub?: string; level?: "ok" | "info" | "warn" | "bad" }) {
  return (
    <div className={`framed ${styles.tile}`}>
      <Marks />
      <div className="eyebrow">{label}</div>
      <div className={styles.tileValue}>
        {level && level !== "info" ? <StatusIcon level={level} size={14} /> : null} <span className="num">{value}</span>
      </div>
      {sub ? <div className={styles.hint}>{sub}</div> : null}
    </div>
  );
}

function Controls({ s, act, busy }: { s: LiveState; act: Act; busy: boolean }) {
  const [confirmStop, setConfirmStop] = useState(false);
  const p = s.phd2;
  const ctl = (action: string, ok: string) => act(() => enginePost("/api/live/guiding/control", { action }), ok);
  const seqRunning = s.sequence.status === "running";
  return (
    <span className={styles.controls}>
      {p.appState !== "Guiding" ? (
        <button type="button" className="button" disabled={busy} onClick={() => ctl("guide", "Guiding started.")}>
          Guide
        </button>
      ) : (
        <>
          <button type="button" className="button" disabled={busy} onClick={() => ctl("dither", "Dither sent.")}>
            Dither
          </button>
          <button type="button" className="button" disabled={busy} onClick={() => ctl(p.paused ? "resume" : "pause", p.paused ? "Resumed." : "Paused.")}>
            {p.paused ? "Resume" : "Pause"}
          </button>
          {confirmStop ? (
            <span className={styles.confirmInline}>
              {seqRunning ? "A sequence is running — NINA expects guiding. " : ""}Stop guiding?{" "}
              <button type="button" className="button" onClick={() => { setConfirmStop(false); void ctl("stop", "Guiding stopped."); }}>
                Stop
              </button>{" "}
              <button type="button" className={styles.link} onClick={() => setConfirmStop(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" className="button" disabled={busy} onClick={() => setConfirmStop(true)}>
              Stop
            </button>
          )}
        </>
      )}
    </span>
  );
}

function GuideStar({ connected }: { connected: boolean }) {
  const [tick, setTick] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const id = setInterval(() => !document.hidden && setTick((t) => t + 1), 3000);
    return () => clearInterval(id);
  }, []);
  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <span className="eyebrow">Guide star</span>
      {connected ? (
        <div className={styles.starBox}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className={styles.star}
            src={`/engine/api/live/guide-star.png?size=31&v=${tick}`}
            alt="PHD2's crop around the guide star"
            onLoad={() => setFailed(false)}
            onError={() => setFailed(true)}
            style={failed ? { visibility: "hidden" } : undefined}
          />
          {failed ? <span className={styles.hint}>No star selected (PHD2 not looping).</span> : null}
        </div>
      ) : (
        <p className={styles.hint}>PHD2 not connected.</p>
      )}
      <p className={styles.hint}>Round and compact is good; elongated means the mount drags along one axis; a faint smear means the sky went.</p>
    </section>
  );
}

function DiagnosisCard({ s, adv }: { s: LiveState; adv: boolean }) {
  const d = s.guiding.diagnosis;
  const st = s.guiding.stats;
  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">Diagnosis</span>
        <Tip tip="Computed from the shape of each axis's error (no AI): overshooting, slow wander, a deadband too wide, chasing seeing — or plain seeing, which no setting fixes.">
          <span className={styles.more}>how?</span>
        </Tip>
      </div>
      {d.axes.map((a) => (
        <div key={a.axis} className={styles.verdict}>
          <StatusIcon level={a.verdict === "healthy" ? "ok" : a.verdict === "seeing_limited" ? "info" : "warn"} size={14} />
          <div>
            <strong>
              {a.axis.toUpperCase()} · {a.title}
            </strong>
            <div>{a.hint}</div>
            {adv ? <div className={styles.hint}>{a.evidence}</div> : null}
          </div>
        </div>
      ))}
      {d.caveats.map((c) => (
        <p key={c} className={styles.hint}>
          <StatusIcon level="info" size={12} /> {c}
        </p>
      ))}
      {adv && st ? (
        <table className={`table ${styles.shape}`}>
          <thead>
            <tr>
              <th />
              <th className="num">
                <Tip tip="Frame-to-frame autocorrelation. Below −0.3: overshooting. Above +0.5: slow wander. Near 0: seeing.">lag-1</Tip>
              </th>
              <th className="num">
                <Tip tip="Signed mean error: a steady offset is drift that is not being corrected.">mean</Tip>
              </th>
              <th className="num">
                <Tip tip="Share of frames with no correction (error inside min-move).">idle</Tip>
              </th>
              <th className="num">
                <Tip tip="Share of consecutive corrections that reversed direction.">reversals</Tip>
              </th>
              <th className="num">pulse</th>
              <th className="num">
                <Tip tip="Share of frames hitting PHD2's maximum pulse duration.">limited</Tip>
              </th>
            </tr>
          </thead>
          <tbody>
            {(["ra", "dec"] as const).map((ax) => (
              <tr key={ax}>
                <td>{ax.toUpperCase()}</td>
                <td className="num">{st[ax].lag1.toFixed(2)}</td>
                <td className="num">{st[ax].mean.toFixed(2)}″</td>
                <td className="num">{Math.round(st[ax].idle * 100)} %</td>
                <td className="num">{Math.round(st[ax].flips * 100)} %</td>
                <td className="num">{st[ax].pulseMs} ms</td>
                <td className="num">{Math.round(st[ax].limited * 100)} %</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {adv && st ? <p className={styles.hint}>RA peak/RMS {st.oscillation.toFixed(2)} (≈1.7 random, above ≈2.2 ringing) · ±{st.se.toFixed(2)}″ standard error on the total.</p> : null}
    </section>
  );
}

function AdvisorCard({ s, act, busy, adv }: { s: LiveState; act: Act; busy: boolean; adv: boolean }) {
  const a = s.advisor;
  const latest = a.analyses[0];
  const toggle = (enabled: boolean) => act(() => enginePost("/api/settings", { advisor: { enabled } }, "PUT"), enabled ? "AI advisor on." : "AI advisor off.");
  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">AI advisor</span>
        <label className={styles.switch}>
          <input type="checkbox" checked={a.enabled} disabled={busy} onChange={(e) => void toggle(e.target.checked)} />
          <span>{a.enabled ? "On" : "Off"}</span>
        </label>
      </div>
      {!a.enabled ? (
        <p className={styles.hint}>
          When on, every {a.intervalMin} min of steady guiding the AI reads the guiding error, the diagnosis, the conditions and your current
          parameters (algorithm, aggressiveness, hysteresis, min-move…) and suggests changes. Nothing is applied until you click Apply, and every
          change is then measured against the 5 minutes before it. It skips the call when there is nothing a setting can fix.
        </p>
      ) : (
        <>
          <div className={styles.advisorHead}>
            <span className={styles.hint}>
              <span className="mono">{a.model}</span>
              {!a.key.ok ? null : ` · every ${a.intervalMin} min`}
            </span>
            <button type="button" className="button" disabled={busy || a.busy || !a.key.ok || !s.phd2.connected} onClick={() => act(() => enginePost("/api/live/advisor/analyze"))}>
              {a.busy ? "Analyzing…" : "Analyze now"}
            </button>
          </div>
          {!a.key.ok ? (
            <p className={styles.error}>
              {a.key.message}. <Link href="/settings?tab=live">Model settings</Link>
            </p>
          ) : null}
          {a.lastError && !latest?.error ? <p className={styles.error}>{a.lastError}</p> : null}
          {a.check && (!latest || a.check.t > latest.t) ? (
            <p className={styles.hint}>
              {clock(a.check.t)} · {a.check.message}
            </p>
          ) : null}
          {latest ? <AnalysisView an={latest} act={act} busy={busy} adv={adv} params={s.phd2.params ?? {}} info={s.paramInfo} /> : <p className={styles.hint}>No analysis yet tonight.</p>}
          {adv && a.analyses.length > 1 ? (
            <details className={styles.older}>
              <summary>Earlier analyses ({a.analyses.length - 1})</summary>
              {a.analyses.slice(1).map((an) => (
                <AnalysisView key={an.id} an={an} act={act} busy={busy} adv={adv} params={s.phd2.params ?? {}} info={s.paramInfo} />
              ))}
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

const pname = (info: LiveState["paramInfo"], name: string) => info[name]?.label ?? name;

function AnalysisView({ an, act, busy, adv, params, info }: { an: Analysis; act: Act; busy: boolean; adv: boolean; params: Record<string, Record<string, number>>; info: LiveState["paramInfo"] }) {
  return (
    <div className={styles.analysis}>
      <div className={styles.hint}>
        {clock(an.t)} · {an.manual ? "asked by you" : "automatic"}
        {an.stats ? ` · total ${an.stats.total.toFixed(2)}″ over ${an.stats.n} frames` : ""}
        {adv && an.latencyMs ? ` · ${(an.latencyMs / 1000).toFixed(1)} s` : ""}
      </div>
      {an.error ? <p className={styles.error}>{an.error}</p> : null}
      {an.assessment ? <p className={styles.assessment}>{an.assessment}</p> : null}
      {!an.error && !an.suggestions.length ? <p className={styles.hint}>No change suggested.</p> : null}
      {an.suggestions.map((sg, i) => (
        <SuggestionRow key={sg.id} sg={sg} first={i === 0} act={act} busy={busy} live={params[sg.axis]?.[sg.param]} label={pname(info, sg.param)} adv={adv} />
      ))}
      {adv && an.notes.length ? (
        <ul className={styles.notes}>
          {an.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function SuggestionRow({ sg, first, act, busy, live, label, adv }: { sg: Suggestion; first: boolean; act: Act; busy: boolean; live: number | undefined; label: string; adv: boolean }) {
  const dir = sg.value > sg.current ? "raise" : "lower";
  return (
    <div className={`${styles.suggestion} ${sg.status !== "open" ? styles.suggestionDone : ""}`}>
      <div>
        <strong>
          {first && sg.status === "open" ? "Suggested: " : ""}
          {dir} {sg.axis.toUpperCase()} {label.toLowerCase()} {sg.current} → {sg.value}
        </strong>
        {adv && label !== sg.param ? <span className={styles.hint}> ({sg.param})</span> : null}
        {sg.why ? <div className={styles.hint}>{sg.why}</div> : null}
        {sg.note ? <div className={styles.hint}>{sg.note}</div> : null}
      </div>
      {sg.status === "open" ? (
        <span className={styles.suggestActions}>
          <button type="button" className="button button-primary" disabled={busy || live === undefined}
            onClick={() => act(() => enginePost(`/api/live/advisor/suggestions/${sg.id}/apply`), `${sg.axis.toUpperCase()} ${label.toLowerCase()} set to ${sg.value}. Measuring for 5 minutes…`)}>
            Apply
          </button>
          <button type="button" className={styles.link} disabled={busy} onClick={() => act(() => enginePost(`/api/live/advisor/suggestions/${sg.id}/dismiss`))}>
            Dismiss
          </button>
        </span>
      ) : (
        <span className={styles.hint}>{sg.status}</span>
      )}
    </div>
  );
}

function Params({ s, act, busy, adv }: { s: LiveState; act: Act; busy: boolean; adv: boolean }) {
  const p = s.phd2;
  const params = p.params ?? {};
  const [draft, setDraft] = useState<Record<string, string>>({});
  const key = (axis: string, name: string) => `${axis}.${name}`;
  const step = (name: string) => (name === "minMove" ? 0.01 : name.toLowerCase().startsWith("aggress") || name === "hysteresis" ? 0.05 : 0.01);
  const exp = p.exposureMs ?? null;
  const choices = Array.from(new Set([...(p.exposureChoices ?? []), ...(exp ? [exp] : [])])).sort((a, b) => a - b);
  const cal = p.calibration;

  const apply = (axis: string, name: string) => {
    const raw = draft[key(axis, name)];
    const v = Number(raw);
    if (raw === undefined || raw.trim() === "" || !Number.isFinite(v)) return;
    void act(async () => {
      await enginePost("/api/live/guiding/param", { axis, param: name, value: v });
      setDraft((d) => {
        const n = { ...d };
        delete n[key(axis, name)];
        return n;
      });
    }, `${axis.toUpperCase()} ${pname(s.paramInfo, name).toLowerCase()} set to ${v}. Measuring for 5 minutes…`);
  };

  return (
    <section className={styles.block}>
      <h2 className="section-title">Guiding parameters</h2>
      <div className={styles.paramGrid}>
        {(["ra", "dec"] as const).map((axis) => {
          const names = Object.keys(params[axis] ?? {}).filter((n) => adv || s.paramInfo[n]?.basic);
          return (
            <div key={axis} className={`framed ${styles.card}`}>
              <Marks />
              <div className={styles.cardHead}>
                <span className="eyebrow">{axis === "ra" ? "RA" : "Dec"}</span>
                <span className={styles.hint}>{p.algorithms?.[axis] ?? "—"}</span>
              </div>
              {names.length ? (
                names.map((n) => {
                  const cur = params[axis][n];
                  const k = key(axis, n);
                  const dirty = draft[k] !== undefined && Number(draft[k]) !== cur;
                  return (
                    <div key={n} className={styles.paramRow}>
                      <Tip tip={`${s.paramInfo[n]?.help ?? "PHD2 parameter."}${adv ? ` (PHD2: ${n})` : ""}`}>
                        <span className={styles.paramLabel}>{pname(s.paramInfo, n)}</span>
                      </Tip>
                      <input
                        className={styles.paramInput}
                        type="number"
                        step={step(n)}
                        min={0}
                        value={draft[k] ?? String(cur)}
                        onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
                        onKeyDown={(e) => e.key === "Enter" && apply(axis, n)}
                        aria-label={`${axis.toUpperCase()} ${pname(s.paramInfo, n)}`}
                      />
                      <button type="button" className="button" disabled={busy || !dirty} onClick={() => apply(axis, n)}>
                        Set
                      </button>
                    </div>
                  );
                })
              ) : (
                <p className={styles.hint}>No parameters reported (PHD2 lists them once the guider is set up).</p>
              )}
            </div>
          );
        })}
        <div className={`framed ${styles.card}`}>
          <Marks />
          <span className="eyebrow">Camera &amp; mode</span>
          <div className={styles.paramRow}>
            <Tip tip="Guide camera exposure. Longer averages out seeing and raises SNR; shorter reacts faster.">
              <span className={styles.paramLabel}>Exposure</span>
            </Tip>
            <select className={styles.paramInput} value={exp ?? ""} disabled={busy || !choices.length}
              onChange={(e) => void act(() => enginePost("/api/live/guiding/exposure", { ms: Number(e.target.value) }), `Guide exposure set to ${Number(e.target.value) / 1000} s.`)}>
              {choices.map((ms) => (
                <option key={ms} value={ms}>
                  {ms / 1000} s
                </option>
              ))}
            </select>
          </div>
          <div className={styles.paramRow}>
            <Tip tip="Auto corrects both ways. North/South only: useful with Dec backlash and a deliberate drift. Off: no Dec guiding.">
              <span className={styles.paramLabel}>Dec guiding</span>
            </Tip>
            <select className={styles.paramInput} value={p.decGuideMode ?? ""} disabled={busy}
              onChange={(e) => void act(() => enginePost("/api/live/guiding/dec-mode", { mode: e.target.value }), `Dec guide mode: ${e.target.value}.`)}>
              {["Auto", "North", "South", "Off"].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          {adv ? (
            <dl className={styles.facts}>
              <div className={styles.fact}>
                <dt>Pixel scale</dt>
                <dd>{fmt(p.pixelScale, 2, "″/px")}</dd>
              </div>
              <div className={styles.fact}>
                <dt>Calibration</dt>
                <dd>{p.calibrated ? (cal ? `RA ${fmt(cal.xRate, 2)} · Dec ${fmt(cal.yRate, 2)} px/s, at Dec ${fmt(cal.declination, 0, "°")}` : "yes") : "not calibrated"}</dd>
              </div>
              {cal?.xAngle != null && cal?.yAngle != null ? (
                <div className={styles.fact}>
                  <dt>Axes angle</dt>
                  <dd>{fmt(Math.abs(((cal.xAngle - cal.yAngle + 540) % 360) - 180), 1, "°")} (90° ideal)</dd>
                </div>
              ) : null}
              {p.profile ? (
                <div className={styles.fact}>
                  <dt>Profile</dt>
                  <dd>{p.profile}</dd>
                </div>
              ) : null}
              {p.version ? (
                <div className={styles.fact}>
                  <dt>PHD2</dt>
                  <dd>{p.version}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </div>
      </div>
      <p className={styles.hint}>
        Changes go to PHD2 immediately (PHD2 may clamp a value; the stored one is shown). The algorithm itself is chosen in PHD2&apos;s Brain.
      </p>
    </section>
  );
}

const VERDICT_LEVEL: Record<Change["verdict"], "ok" | "info" | "warn" | "bad"> = { better: "ok", neutral: "info", worse: "bad", void: "info", pending: "info" };

function Changes({ changes, act, busy, connected, adv }: { changes: Change[]; act: Act; busy: boolean; connected: boolean; adv: boolean }) {
  if (!changes.length) return null;
  return (
    <section className={styles.block}>
      <h2 className="section-title">Changes tonight</h2>
      <div className={styles.scroller}>
        <table className="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Change</th>
              <th>By</th>
              <th>Result (5 min before vs after)</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {changes.map((c) => (
              <tr key={c.id}>
                <td className="num">{clock(c.t)}</td>
                <td>
                  {c.axis === "both" ? "" : `${c.axis.toUpperCase()} `}
                  {c.param} <span className="num">{c.old} → {c.new}</span>
                </td>
                <td className={styles.hint}>{c.source === "ai" ? "AI suggestion" : c.source === "revert" ? "revert" : "you"}</td>
                <td>
                  <StatusIcon level={VERDICT_LEVEL[c.verdict]} size={13} label={c.verdict} />{" "}
                  {c.verdict === "pending" ? `measuring… result ${clock(c.measureAt)}` : c.outcome}
                  {adv && c.before && c.after ? (
                    <span className={styles.hint}>
                      {" "}
                      · RA {c.before.ra.toFixed(2)}→{c.after.ra.toFixed(2)} · Dec {c.before.dec.toFixed(2)}→{c.after.dec.toFixed(2)}
                    </span>
                  ) : null}
                </td>
                <td>
                  {connected && (c.axis === "ra" || c.axis === "dec") ? (
                    <button type="button" className={styles.link} disabled={busy} onClick={() => act(() => enginePost(`/api/live/advisor/changes/${c.id}/revert`), `Reverted ${c.param} to ${c.old}.`)}>
                      Revert to {c.old}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
