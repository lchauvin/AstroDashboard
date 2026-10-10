"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Play, RotateCw, Search } from "lucide-react";

import type { DiagJob, DiagResultRow, SessionIndexRow } from "@/lib/diagnostics";
import { filterLetter } from "@/lib/diagnostics";
import { hours, relativeTime, shortDate } from "@/lib/format";
import { FilterChip, StatusIcon, Tip } from "../ui";
import { jobLabel, latestJob, useDiagnostics } from "./useJobs";
import tableStyles from "../TargetsTable.module.css";
import styles from "./Diagnostics.module.css";

type Show = "all" | "pending" | "done";

/** Findings folded by kind: a mosaic repeats the same problem once per panel and filter. */
function kinds(findings: NonNullable<DiagResultRow["findings"]>) {
  const m = new Map<string, { title: string; short: string; n: number; frames: number }>();
  for (const f of findings) {
    const title = f.code === "flat-mismatch" ? "Flats don't match the lights" : f.title;
    const k = m.get(f.code) ?? { title, short: f.code === "flat-mismatch" ? "Flats" : title.split(" ")[0], n: 0, frames: 0 };
    k.n += 1;
    k.frames += f.frames;
    m.set(f.code, k);
  }
  return [...m.values()];
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
        <table className="table">
          <thead>
            <tr>
              <th>Night</th>
              <th>Target</th>
              <th>Lights</th>
              <th>Result (kept / measured)</th>
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
              return (
                <tr key={`${s.revisionId}/${s.folder}`}>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <Link href={href} className={styles.nightLink}>
                      {shortDate(s.date)}
                    </Link>
                    <div className="muted mono" style={{ fontSize: 11 }}>
                      {s.folder}
                    </div>
                  </td>
                  <td>
                    <Link href={`/library/${s.revisionId}`} className={styles.targetLink}>
                      {s.catalogId}
                    </Link>
                    <div className="muted" style={{ fontSize: 13 }}>
                      {s.name}
                    </div>
                  </td>
                  <td className="num" style={{ whiteSpace: "nowrap" }}>
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
                      <span className={styles.chips}>
                        {r.summary.lights.map((g) => (
                          <Tip
                            key={g.key}
                            tip={
                              <span className="num">
                                {g.key}: {g.kept} kept of {g.frames} · {hours(g.keptSeconds)}
                                {g.fwhm != null ? ` · FWHM ${g.fwhm.toFixed(2)}″` : ""}
                              </span>
                            }
                          >
                            <span className={styles.chip}>
                              <FilterChip filter={filterLetter(g.filter)} />
                              <span className="num">
                                {g.kept}/{g.frames}
                              </span>
                            </span>
                          </Tip>
                        ))}
                        <span className="muted" style={{ fontSize: 11 }}>
                          {relativeTime(new Date(r.finished * 1000).toISOString())}
                        </span>
                      </span>
                    ) : failed ? (
                      <span>
                        <StatusIcon level="bad" /> Failed — open for details
                      </span>
                    ) : (
                      <span className="muted">Not analysed</span>
                    )}
                  </td>
                  <td>
                    {r ? (
                      <span className={styles.chips}>
                        {r.summary.flats.map((f) => (
                          <StatusIcon
                            key={f.key}
                            size={13}
                            level={f.verdict === "good" ? "ok" : f.verdict === "warn" ? "warn" : "bad"}
                            label={`${f.key}: ${f.verdict ?? "?"}`}
                          />
                        ))}
                        {!r.summary.flats.length ? <span className="muted">none</span> : null}
                      </span>
                    ) : null}
                  </td>
                  <td>
                    {r?.findings?.length ? (
                      <Tip
                        tip={
                          <span>
                            {kinds(r.findings).map((k) => (
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
                          <StatusIcon level="warn" size={13} label="Findings" />
                          <span className="num">{kinds(r.findings).length}</span>
                          <span className="muted" style={{ fontSize: 12 }}>
                            {kinds(r.findings)
                              .slice(0, 3)
                              .map((k) => k.short)
                              .join(", ")}
                          </span>
                        </span>
                      </Tip>
                    ) : r ? (
                      <StatusIcon level="ok" size={13} label="No findings" />
                    ) : null}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {!j ? (
                      <button type="button" className={styles.action} onClick={() => run(s.revisionId, s.folder)}>
                        {r ? <RotateCw size={12} strokeWidth={1.8} /> : <Play size={12} strokeWidth={1.8} />}
                        {r ? "Re-run" : "Analyse"}
                      </button>
                    ) : null}
                    {r ? (
                      <Link href={href} className={styles.action}>
                        Open
                      </Link>
                    ) : null}
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
