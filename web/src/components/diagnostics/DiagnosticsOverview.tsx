"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { Play, RotateCw, Search } from "lucide-react";

import type { DiagJob, DiagResultRow, SessionIndexRow } from "@/lib/diagnostics";
import { filterLetter } from "@/lib/diagnostics";
import { hours, relativeTime, shortDate } from "@/lib/format";
import { Badge, FilterChip, StatusIcon, Tip } from "../ui";
import { jobLabel, latestJob, useDiagnostics } from "./useJobs";
import tableStyles from "../TargetsTable.module.css";
import styles from "./Diagnostics.module.css";

type Show = "all" | "pending" | "done";

/** Findings folded by kind: a mosaic repeats the same problem once per panel and filter. */
function kinds(findings: NonNullable<DiagResultRow["findings"]>) {
  const m = new Map<string, { title: string; n: number; frames: number }>();
  for (const f of findings) {
    const title = f.code === "flat-mismatch" ? "Flats don't match the lights" : f.title;
    const k = m.get(f.code) ?? { title, n: 0, frames: 0 };
    k.n += 1;
    k.frames += f.frames;
    m.set(f.code, k);
  }
  return [...m.values()];
}

type Lights = DiagResultRow["summary"]["lights"];

/** Light groups folded by filter (a mosaic has one group per panel and filter), FWHM weighted by kept frames. */
function byFilter(lights: Lights) {
  const m = new Map<string, { filter: string; frames: number; kept: number; keptSeconds: number; fw: number; fwN: number }>();
  for (const g of lights) {
    const f = m.get(g.filter) ?? { filter: g.filter, frames: 0, kept: 0, keptSeconds: 0, fw: 0, fwN: 0 };
    f.frames += g.frames;
    f.kept += g.kept;
    f.keptSeconds += g.keptSeconds;
    if (g.fwhm != null && g.kept) {
      f.fw += g.fwhm * g.kept;
      f.fwN += g.kept;
    }
    m.set(g.filter, f);
  }
  return [...m.values()];
}

/** Distinct mosaic panels named in the group keys ("M 31 Panel 2 · Ha"); 0 for a single field. */
function panelCount(lights: Lights) {
  const ids = new Set(lights.map((g) => /panel[\s_-]*(\d+)/i.exec(g.key)?.[1]).filter(Boolean));
  return ids.size > 1 ? ids.size : 0;
}

type Verdict = "good" | "warn" | "bad" | null;
const VERDICT_RANK = { good: 0, warn: 1, bad: 2 } as const;
const verdictLevel = (v: Verdict) => (v === "good" ? "ok" : v === "warn" ? "warn" : "bad");

function ResultCell({ r }: { r: DiagResultRow }) {
  const lights = r.summary.lights;
  const frames = lights.reduce((a, g) => a + g.frames, 0);
  const kept = lights.reduce((a, g) => a + g.kept, 0);
  const keptSeconds = lights.reduce((a, g) => a + g.keptSeconds, 0);
  const ratio = frames ? kept / frames : 0;
  const level = ratio >= 0.75 ? "good" : ratio >= 0.5 ? "warn" : "bad";
  return (
    <Tip
      tip={
        <span className="num">
          <span className={styles.tipGrid}>
            {byFilter(lights).map((f) => (
              <Fragment key={f.filter}>
                <FilterChip filter={filterLetter(f.filter)} />
                <span>
                  {f.kept}/{f.frames}
                </span>
                <span>{hours(f.keptSeconds)}</span>
                <span>{f.fwN ? `${(f.fw / f.fwN).toFixed(2)}″` : ""}</span>
              </Fragment>
            ))}
          </span>
          <span className="muted">Analysed {relativeTime(new Date(r.finished * 1000).toISOString())}</span>
        </span>
      }
    >
      <span className={styles.keptCell}>
        <span className={styles.keptBar} aria-hidden="true">
          <span style={{ width: `${100 * ratio}%`, background: `var(--status-${level})` }} />
        </span>
        <span className="num">
          {Math.round(100 * ratio)} % kept · {hours(keptSeconds)}
        </span>
      </span>
    </Tip>
  );
}

function FlatsCell({ flats }: { flats: DiagResultRow["summary"]["flats"] }) {
  if (!flats.length) return <span className="muted">none</span>;
  const rank = (v: Verdict) => (v ? VERDICT_RANK[v] : 2);
  const worst = flats.reduce<Verdict>((w, f) => (rank(f.verdict) > rank(w) ? f.verdict : w), "good");
  const off = flats.filter((f) => f.verdict !== "good").length;
  return (
    <Tip
      tip={
        <span>
          {flats.map((f) => (
            <span key={f.key} className={styles.chip}>
              <StatusIcon size={12} level={verdictLevel(f.verdict)} label={f.verdict ?? "unknown"} />
              {f.key}
              <br />
            </span>
          ))}
        </span>
      }
    >
      <span className={styles.chip}>
        <StatusIcon size={13} level={verdictLevel(worst)} label={off ? `${off} of ${flats.length} flat sets need attention` : "All flats good"} />
        {off ? (
          <span className="num">
            {off}/{flats.length}
          </span>
        ) : null}
      </span>
    </Tip>
  );
}

export function DiagnosticsOverview({
  sessions,
  initialJobs,
  initialResults,
}: {
  sessions: SessionIndexRow[];
  initialJobs: DiagJob[];
  initialResults: DiagResultRow[];
}) {
  const { jobs, results, run } = useDiagnostics(initialJobs, initialResults);
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("all");

  const resultFor = useMemo(() => {
    const m = new Map<string, DiagResultRow>();
    for (const r of results) m.set(`${r.revisionId}/${r.session}`, r);
    return m;
  }, [results]);

  const visible = sessions.filter((s) => {
    const q = query.trim().toLowerCase();
    if (q && !`${s.catalogId} ${s.name} ${s.folder}`.toLowerCase().includes(q)) return false;
    const done = resultFor.has(`${s.revisionId}/${s.folder}`);
    return show === "all" || (show === "done" ? done : !done);
  });
  const pending = visible.filter((s) => !resultFor.has(`${s.revisionId}/${s.folder}`));
  const busy = (s: SessionIndexRow) => {
    const j = latestJob(jobs, s.revisionId, s.folder);
    return j && (j.status === "queued" || j.status === "running") ? j : undefined;
  };
  const queued = jobs.filter((j) => j.status === "queued" || j.status === "running").length;

  return (
    <section>
      <div className={tableStyles.controls}>
        <label className={tableStyles.search}>
          <Search size={14} strokeWidth={1.8} aria-hidden="true" />
          <input type="search" placeholder="Search targets" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search targets" />
        </label>
        <div className={tableStyles.presets} role="group" aria-label="Show">
          {(
            [
              ["all", "All nights"],
              ["pending", "Not analysed"],
              ["done", "Analysed"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={show === id ? `${tableStyles.preset} ${tableStyles.presetActive}` : tableStyles.preset}
              aria-pressed={show === id}
              onClick={() => setShow(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className={styles.queueInfo}>{queued ? `${queued} job${queued > 1 ? "s" : ""} in the queue` : ""}</span>
        <button
          type="button"
          className="button"
          disabled={!pending.length}
          onClick={async () => {
            for (const s of pending) if (!busy(s)) await run(s.revisionId, s.folder);
          }}
          title="Queue every night shown that has not been analysed yet"
        >
          <Play size={12} strokeWidth={1.8} /> Analyse {pending.length} shown
        </button>
      </div>

      <div className={tableStyles.scroller}>
        <table className={`table ${styles.overviewTable}`}>
          <thead>
            <tr>
              <th>Night</th>
              <th>Target</th>
              <th>Lights</th>
              <th>Result</th>
              <th>Flats</th>
              <th>Findings</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {visible.map((s) => {
              const r = resultFor.get(`${s.revisionId}/${s.folder}`);
              const j = busy(s);
              const failed = latestJob(jobs, s.revisionId, s.folder)?.status === "failed";
              const href = `/diagnostics/${s.revisionId}/${encodeURIComponent(s.folder)}`;
              const panels = r ? panelCount(r.summary.lights) : 0;
              const found = r?.findings?.length ? kinds(r.findings) : [];
              return (
                <tr key={`${s.revisionId}/${s.folder}`}>
                  <td>
                    <Link href={href} className={styles.nightLink} title={s.folder}>
                      {shortDate(s.date)}
                    </Link>
                  </td>
                  <td>
                    <span className={styles.target}>
                      <Link href={`/library/${s.revisionId}`} className={styles.targetLink}>
                        {s.catalogId}
                      </Link>
                      {s.name && s.name !== s.catalogId ? (
                        <span className={`muted ${styles.targetName}`} title={s.name}>
                          {s.name}
                        </span>
                      ) : null}
                      {panels ? <Badge tone="muted">{panels} panels</Badge> : null}
                    </span>
                  </td>
                  <td className="num">
                    {s.lights} · {hours(s.seconds)}
                  </td>
                  <td>
                    {j ? (
                      <span className={styles.progress}>
                        <span className={styles.progressBar}>
                          <span style={{ width: `${j.total ? (100 * j.done) / j.total : 0}%` }} />
                        </span>
                        {jobLabel(j)}
                      </span>
                    ) : r ? (
                      <ResultCell r={r} />
                    ) : failed ? (
                      <span>
                        <StatusIcon level="bad" size={13} /> Failed — open for details
                      </span>
                    ) : (
                      <span className="muted">Not analysed</span>
                    )}
                  </td>
                  <td>{r ? <FlatsCell flats={r.summary.flats} /> : null}</td>
                  <td>
                    {found.length ? (
                      <Tip
                        tip={
                          <span>
                            {found.map((k) => (
                              <span key={k.title}>
                                {k.title}
                                {k.n > 1 ? ` ×${k.n}` : ""} ({k.frames} frame{k.frames === 1 ? "" : "s"})
                                <br />
                              </span>
                            ))}
                          </span>
                        }
                      >
                        <span className={styles.chip}>
                          <StatusIcon level="warn" size={13} label={`${found.length} finding${found.length > 1 ? "s" : ""}`} />
                          <span className="num">{found.length}</span>
                        </span>
                      </Tip>
                    ) : r ? (
                      <StatusIcon level="ok" size={13} label="No findings" />
                    ) : null}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {j ? null : r ? (
                      <button type="button" className={styles.action} onClick={() => run(s.revisionId, s.folder)} title="Re-run the analysis" aria-label="Re-run the analysis">
                        <RotateCw size={12} strokeWidth={1.8} />
                      </button>
                    ) : (
                      <button type="button" className={styles.action} onClick={() => run(s.revisionId, s.folder)}>
                        <Play size={12} strokeWidth={1.8} /> Analyse
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
