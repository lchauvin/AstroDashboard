"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { clock, duration, enginePost, filterLetterOf, fmt, type DeviceCard, type Frame, type IngestStatus, type LiveState, type SeqStep } from "@/lib/live";
import { FilterChip, Marks, StatusIcon, Tip } from "../ui";
import { LiveBar, NotConnected } from "./LiveBar";
import styles from "./live.module.css";
import { TimeChart } from "./TimeChart";
import { TonightTarget } from "./TonightTarget";
import { useAdvanced, useLive } from "./useLive";

const RA = "var(--filter-B)";
const DEC = "var(--filter-H)";

/** `beforeFrames` is rendered just above Tonight's frames (the page puts Auto-ingest there). */
export function Overview({ initial, ingest, beforeFrames }: { initial: LiveState | null; ingest: IngestStatus | null; beforeFrames?: React.ReactNode }) {
  const { data: s, offline, refresh } = useLive(initial, 180);
  const [adv] = useAdvanced();
  if (!s)
    return (
      <div className={styles.live}>
        <LiveBar s={null} offline tab="overview" />
        {beforeFrames}
      </div>
    );
  return (
    <div className={styles.live}>
      <LiveBar s={s} offline={offline} tab="overview" />
      {!s.nina.connected ? <NotConnected what="NINA" error={s.nina.error} where={s.nina.url} /> : null}
      <div className={styles.topGrid}>
        <LatestFrame s={s} adv={adv} />
        <div className={styles.sideCol}>
          <SequenceCard s={s} adv={adv} refresh={refresh} />
          <GuidingMini s={s} />
          <TonightTarget initial={ingest} />
        </div>
      </div>
      {s.equipment.length ? (
        <section className={styles.block}>
          <h2 className="section-title">Equipment</h2>
          <div className={styles.devices}>
            {s.equipment.map((d) => (
              <Device key={d.key} d={d} adv={adv} />
            ))}
          </div>
        </section>
      ) : null}
      {adv && s.sequence.available ? <SequenceTree steps={s.sequence.steps} /> : null}
      {beforeFrames}
      <Frames s={s} adv={adv} />
    </div>
  );
}

function LatestFrame({ s, adv }: { s: LiveState; adv: boolean }) {
  const l = s.latest;
  const r = l?.record;
  return (
    <section className={`framed ${styles.card} ${styles.frameCard}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">Last image</span>
        {l ? <span className={styles.hint}>{r?.date ? clock(r.date) : ""}{adv ? ` · ${l.source === "file" ? ".Download" : "from NINA"} · ${l.name ?? ""}` : ""}</span> : null}
      </div>
      {l ? (
        <a href={`/engine/api/live/frame.jpg?v=${l.token}`} target="_blank" rel="noreferrer" className={styles.frameLink}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className={styles.frame} src={`/engine/api/live/frame.jpg?v=${l.token}`} alt={`Last image${r?.target ? ` of ${r.target}` : ""}, stretched`} />
        </a>
      ) : (
        <p className={styles.hint}>No image yet tonight.</p>
      )}
      {r ? (
        <div className={styles.frameFacts}>
          <span className={styles.frameTarget}>{r.target ?? r.type ?? "—"}</span>
          {r.filter ? <FilterChip filter={filterLetterOf(r.filter)} /> : null}
          <span>{fmt(r.exposure, 0, " s")}</span>
          <span>HFR <b className="num">{fmt(r.hfr, 2)}</b></span>
          <span>Stars <b className="num">{r.stars ?? "—"}</b></span>
          {adv ? <span>{fmt(r.temperature, 1, " °C")} · gain {r.gain ?? "—"}</span> : null}
          {r.flags.length ? (
            <span className={styles.flagged}>
              <StatusIcon level="warn" size={13} /> {r.flags.join(" · ")}
            </span>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

const SEQ_STATE: Record<string, { label: string; level: "ok" | "info" | "warn" }> = {
  running: { label: "Running", level: "ok" },
  idle: { label: "Not running", level: "info" },
  finished: { label: "Finished", level: "info" },
  none: { label: "No sequence loaded", level: "warn" },
};

function SequenceCard({ s, adv, refresh }: { s: LiveState; adv: boolean; refresh: () => Promise<unknown> }) {
  const q = s.sequence;
  const cur = q.current;
  const info = cur?.info ?? {};
  const st = SEQ_STATE[q.status] ?? SEQ_STATE.none;
  // Exposure count: the running step's own, else the nearest enclosing loop's (e.g. a Take Exposure inside a Smart Exposure).
  const byId = new Map(q.steps.map((x) => [x.id, x]));
  let iters: { done: number; total: number } | null = null;
  for (let x: SeqStep | undefined = cur ?? undefined; x && !iters; x = x.parent ? byId.get(x.parent) : undefined) {
    if (x.info.Iterations && x.info.CompletedIterations != null) iters = { done: x.info.CompletedIterations, total: x.info.Iterations };
  }
  const exp = s.exposure;
  // Both progress rows stay in place while the sequence runs, so the card does not jump between frames.
  const showRows = q.status === "running" || !!exp?.left;
  const camState = exp?.state ? exp.state.charAt(0).toUpperCase() + exp.state.slice(1).toLowerCase() : "Camera not connected";
  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">Sequence</span>
        <span className={styles.state}>
          <StatusIcon level={st.level} size={13} /> {st.label}
        </span>
      </div>
      {s.nina.connected ? (
        <>
          <div className={styles.big}>{q.target ?? s.latest?.record?.target ?? "—"}</div>
          {cur ? (
            <div className={styles.stepLine}>
              <span>{cur.name}</span>
              {info.Filter ? <FilterChip filter={filterLetterOf(info.Filter)} /> : null}
              {info.ExposureTime ? <span className={styles.hint}>{info.ExposureTime} s</span> : null}
              {adv && info.Gain != null ? <span className={styles.hint}>gain {info.Gain}</span> : null}
            </div>
          ) : null}
          {showRows ? (
            <>
              <Progress label="Exposures" done={iters?.done ?? 0} total={iters?.total ?? 1} text={iters ? `${iters.done} / ${iters.total}` : "—"} />
              <Progress
                label="This exposure"
                done={exp?.left != null && exp.total ? exp.total - exp.left : 0}
                total={exp?.total ?? 1}
                text={exp?.left != null ? `${Math.round(exp.left)} s left` : camState}
              />
            </>
          ) : null}
          {adv ? (
            <p className={styles.hint}>
              {q.path.join(" › ")} · {q.done}/{q.total} steps done
            </p>
          ) : null}
          {q.error && adv ? <p className={styles.hint}>{q.error}</p> : null}
        </>
      ) : (
        <p className={styles.hint}>Waiting for NINA.</p>
      )}
      {s.guard ? <GuardToggle g={s.guard} refresh={refresh} /> : null}
    </section>
  );
}

/** The sequence guard's on/off; its timings are in Settings → Live · Ingest, its state on the Live bar. */
function GuardToggle({ g, refresh }: { g: NonNullable<LiveState["guard"]>; refresh: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toggle = async (enabled: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await enginePost("/api/settings", { guard: { enabled } }, "PUT");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={styles.guardToggle}>
      <Tip tip="Stops the NINA sequence when the guide star has been lost (clouds) for a while, and starts it again once PHD2 has guided steadily. NINA's pre-sequence checklist is skipped on resume. A meridian flip, autofocus or centering never counts.">
        <label className={styles.switch}>
          <input type="checkbox" checked={g.enabled} disabled={busy} onChange={(e) => void toggle(e.target.checked)} />
          <span>Pause when guiding is lost</span>
        </label>
      </Tip>
      <span className={styles.hint}>
        stop after {g.stopAfterS} s lost · resume after {g.resumeAfterS} s steady · <Link href="/settings?tab=live">timings</Link>
      </span>
      {error ? <span className={styles.error}>{error}</span> : null}
    </div>
  );
}

function Progress({ label, done, total, text }: { label: string; done: number; total: number; text: string }) {
  const pct = total > 0 ? Math.min(100, Math.max(0, (100 * done) / total)) : 0;
  return (
    <div className={styles.progress}>
      <div className={styles.progressHead}>
        <span className={styles.factLabel}>{label}</span>
        <span className="num">{text}</span>
      </div>
      <div className={styles.track} role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={styles.fill} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function GuidingMini({ s }: { s: LiveState }) {
  const g = s.guiding;
  const st = g.stats;
  const p = s.phd2;
  const target = s.advisor.targetRms;
  const level = !st ? "info" : st.total <= target ? "ok" : st.total <= target * 1.5 ? "warn" : "bad";
  const activity = g.active.filter((a) => ["dither", "settling", "star lost", "calibrating", "paused", "meridian flip", "autofocus"].includes(a));
  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">Guiding</span>
        <Link href="/live/guiding" className={styles.more}>
          Details →
        </Link>
      </div>
      {!p.connected ? (
        <p className={styles.hint}>PHD2 not connected ({p.address}).</p>
      ) : (
        <>
          <div className={styles.rmsRow}>
            <span className={styles.rmsBig}>
              <StatusIcon level={level} size={14} label={st ? (level === "ok" ? "On target" : "Above target") : "No data"} />
              <b className="num">{fmt(st?.total, 2, "″")}</b>
              <span className={styles.hint}>total</span>
            </span>
            <span>
              RA <b className="num">{fmt(st?.ra.rms, 2)}</b>
            </span>
            <span>
              Dec <b className="num">{fmt(st?.dec.rms, 2)}</b>
            </span>
          </div>
          <div className={styles.hint}>
            {p.appState}
            {activity.length ? ` · ${activity.join(", ")}` : ""}
            {p.guidingForS ? ` · for ${duration(p.guidingForS)}` : ""} · target {target.toFixed(2)}″
          </div>
          {g.graph.t.length > 2 ? (
            <TimeChart
              t={g.graph.t}
              seconds={180}
              unit="″"
              height={120}
              excluded={g.graph.excluded}
              series={[
                { key: "ra", label: "RA", color: RA, values: g.graph.ra },
                { key: "dec", label: "Dec", color: DEC, values: g.graph.dec },
              ]}
              ariaLabel="RA and Dec guide error, last 3 minutes"
            />
          ) : (
            <p className={styles.hint}>No guide steps yet.</p>
          )}
        </>
      )}
    </section>
  );
}

function Device({ d, adv }: { d: DeviceCard; adv: boolean }) {
  const facts = d.facts.filter((f) => adv || !f.advanced);
  const body = (
    <>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">{d.label}</span>
        <span className={styles.state}>
          {d.level ? <StatusIcon level={d.level} size={13} /> : null}
          {d.status}
        </span>
      </div>
      {d.name ? <div className={styles.deviceName}>{d.name}</div> : null}
      {facts.length ? (
        <dl className={styles.facts}>
          {facts.map((f) => (
            <div key={f.label} className={styles.fact}>
              <dt>{f.label}</dt>
              <dd className={f.level ? styles[`lvl_${f.level}`] : undefined}>
                {f.level ? <StatusIcon level={f.level} size={12} /> : null} {f.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </>
  );
  return d.key === "phd2" ? (
    <Link href="/live/guiding" className={`framed ${styles.card} ${styles.device} ${styles.deviceLink}`}>
      {body}
    </Link>
  ) : (
    <section className={`framed ${styles.card} ${styles.device}`}>{body}</section>
  );
}

function SequenceTree({ steps }: { steps: SeqStep[] }) {
  return (
    <section className={styles.block}>
      <h2 className="section-title">Sequence</h2>
      <ol className={styles.tree}>
        {steps.map((st) => (
          <li key={st.id} style={{ paddingLeft: st.depth * 18 }} className={st.status === "RUNNING" ? styles.treeOn : st.status === "FINISHED" || st.status === "SKIPPED" ? styles.treeDone : undefined}>
            <span className={styles.treeState}>{st.status.toLowerCase() || "—"}</span>
            <span className={st.container ? styles.treeContainer : undefined}>{st.name}</span>
            {st.info.Filter ? <FilterChip filter={filterLetterOf(st.info.Filter)} /> : null}
            {st.info.ExposureTime ? <span className={styles.hint}>{st.info.ExposureTime} s</span> : null}
            {st.info.Iterations ? <span className={styles.hint}>{st.info.CompletedIterations ?? 0}/{st.info.Iterations}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

const FILTER_ORDER = ["L", "R", "G", "B", "H", "O", "S"];

type FramesData = { total: number; counts: Record<string, number>; selected: number; frames: (Frame & { filterKey?: string })[] };
const PAGE = 50;

function Frames({ s, adv }: { s: LiveState; adv: boolean }) {
  const [only, setOnly] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const [data, setData] = useState<FramesData | null>(null);
  const newest = s.frames[0]?.index; // a new frame: fetch again at once

  // The whole night comes from its own endpoint (counts over every frame, the newest `limit` of one filter);
  // the 2-second live snapshot only carries the most recent frames.
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const q = new URLSearchParams({ all: adv ? "true" : "false", limit: String(limit) });
        if (only) q.set("filter", only);
        const res = await fetch(`/engine/api/live/frames?${q}`, { cache: "no-store" });
        if (res.ok && !stop) setData(await res.json());
      } catch {}
    };
    void load();
    const id = setInterval(() => !document.hidden && void load(), 10000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [adv, only, limit, newest]);

  const keyOf = (f: Frame & { filterKey?: string }) => f.filterKey ?? filterLetterOf(f.filter) ?? "—";
  // Until the endpoint answers (or with an engine that predates it), use the snapshot's recent frames.
  const local = adv ? s.frames : s.frames.filter((f) => f.type === "LIGHT");
  const counts = new Map<string, number>(data ? Object.entries(data.counts) : []);
  if (!data) for (const f of local) counts.set(keyOf(f), (counts.get(keyOf(f)) ?? 0) + 1);
  const total = data ? data.total : local.length;
  // One button per filter shot tonight, in the dashboard's filter order, each with its count.
  const keys = [...counts.keys()].sort((a, b) => {
    const ia = FILTER_ORDER.indexOf(a), ib = FILTER_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const active = only && counts.has(only) ? only : null;
  const frames = (data ? data.frames : local).filter((f) => !active || keyOf(f) === active);
  const selected = data ? (active ? counts.get(active) ?? 0 : total) : frames.length;
  const choose = (k: string | null) => {
    setOnly(k);
    setLimit(PAGE);
  };
  return (
    <section className={styles.block}>
      <div className={styles.framesHead}>
        <h2 className="section-title">Tonight&apos;s frames</h2>
        {keys.length > 1 ? (
          <div className={styles.seg} role="group" aria-label="Show frames of one filter">
            <button type="button" className={!active ? styles.segOn : undefined} aria-pressed={!active} onClick={() => choose(null)}>
              All <span className={styles.segCount}>{total}</span>
            </button>
            {keys.map((k) => (
              <button key={k} type="button" className={active === k ? styles.segOn : undefined} aria-pressed={active === k} onClick={() => choose(active === k ? null : k)}>
                {k === "—" ? "No filter" : <FilterChip filter={k} />} <span className={styles.segCount}>{counts.get(k)}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {frames.length ? (
        <div className={styles.scroller}>
          <table className="table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Target</th>
                {adv ? <th>Type</th> : null}
                <th>Filter</th>
                <th className="num">Exp</th>
                <th className="num">HFR</th>
                <th className="num">Stars</th>
                {adv ? (
                  <>
                    <th className="num">Temp</th>
                    <th className="num">Median</th>
                    <th className="num">Guiding</th>
                  </>
                ) : null}
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {frames.slice(0, limit).map((f) => (
                <tr key={f.index}>
                  <td className="num">{clock(f.date)}</td>
                  <td>{f.target ?? "—"}</td>
                  {adv ? <td className={styles.hint}>{f.type?.toLowerCase()}</td> : null}
                  <td>{f.filter ? <FilterChip filter={filterLetterOf(f.filter)} /> : "—"}</td>
                  <td className="num">{fmt(f.exposure, 0, " s")}</td>
                  <td className="num">{fmt(f.hfr, 2)}</td>
                  <td className="num">{f.stars ?? "—"}</td>
                  {adv ? (
                    <>
                      <td className="num">{fmt(f.temperature, 1)}</td>
                      <td className="num">{fmt(f.median, 0)}</td>
                      <td className="num">{f.rms ?? "—"}</td>
                    </>
                  ) : null}
                  <td>
                    {f.flags.length ? (
                      <Tip tip="Compared with the previous frames of the same filter.">
                        <span className={styles.flagged}>
                          <StatusIcon level="warn" size={12} /> {f.flags.join(" · ")}
                        </span>
                      </Tip>
                    ) : (
                      <span className={styles.hint}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected > Math.min(frames.length, limit) ? (
            <p className={styles.hint} style={{ marginTop: 8 }}>
              Showing the newest {Math.min(frames.length, limit)} of {selected}.{" "}
              <button type="button" className={styles.link} onClick={() => setLimit(5000)}>
                Show all
              </button>
            </p>
          ) : null}
        </div>
      ) : (
        <p className={styles.hint}>No light frames yet tonight.</p>
      )}
    </section>
  );
}
