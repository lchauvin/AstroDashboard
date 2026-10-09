"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, RotateCw } from "lucide-react";

import type { DiagDetail, DiagJob, LightFrame, Note } from "@/lib/diagnostics";
import { FLAG_LABELS, filterLetter, reasonLabel } from "@/lib/diagnostics";
import { hours, num, relativeTime } from "@/lib/format";
import type { Level } from "@/lib/types";
import { FilterChip, Marks, StatusIcon } from "../ui";
import { Findings } from "./Findings";
import { FrameTable } from "./FrameTable";
import { PreviewModal } from "./PreviewModal";
import { Timeline } from "./Timeline";
import { jobLabel } from "./useJobs";
import styles from "./Diagnostics.module.css";

const NOTE_LEVEL: Record<Note["level"], Level> = { ok: "ok", info: "info", warn: "warn", bad: "bad" };
const CAL_LEVEL: Record<string, Level> = { ok: "ok", warn: "warn", bad: "bad", missing: "bad" };

export function SessionDiagnostics({ detail }: { detail: DiagDetail }) {
  const router = useRouter();
  const [job, setJob] = useState<DiagJob | null>(detail.job);
  const [selected, setSelected] = useState<{ list: { path: string; file: string; caption: string }[]; index: number } | null>(null);
  const active = job && (job.status === "queued" || job.status === "running");
  const url = `/engine/api/diagnostics/${detail.revision.id}/${encodeURIComponent(detail.session.folder)}`;

  useEffect(() => setJob(detail.job), [detail.job]);
  useEffect(() => {
    if (!active) return;
    const t = setTimeout(async () => {
      try {
        const res = await fetch(url, { cache: "no-store" });
        const body = (await res.json()) as DiagDetail;
        setJob(body.job);
        if (body.job && !["queued", "running"].includes(body.job.status)) router.refresh();
      } catch {
        /* retry on next tick */
      }
    }, 1000);
    return () => clearTimeout(t);
  }, [active, job, url, router]);

  async function run() {
    const res = await fetch("/engine/api/diagnostics/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revisionId: detail.revision.id, session: detail.session.folder }),
    });
    const body = (await res.json()) as { jobs: DiagJob[] };
    setJob(body.jobs[0] ?? null);
  }

  const stored = detail.stored;
  const r = stored?.result;
  const lights = r?.lights ?? [];
  const analysis = detail.analysis;
  const flags = analysis?.flags ?? {};
  const caption = (f: LightFrame) => {
    const found = (flags[f.path] ?? []).map((c) => FLAG_LABELS[c] ?? c);
    return [
      f.filter_name ?? "",
      f.rejected ? "rejected: " + f.reasons.map(reasonLabel).join(", ") : "kept",
      `FWHM ${num(f.fwhm_median, 2)}″`,
      `${num(f.n_stars)} stars`,
      `score ${num(f.score, 2)}`,
      found.length ? `findings: ${found.join(", ")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  };
  const openFrames = (frames: LightFrame[], index: number) =>
    setSelected({ list: frames.map((f) => ({ path: f.path, file: f.file, caption: caption(f) })), index });
  const openPaths = (paths: string[]) => {
    const wanted = new Set(paths);
    const frames = lights
      .flatMap((g) => g.frames)
      .filter((f) => wanted.has(f.path))
      .sort((a, b) => (a.obs_time ?? "").localeCompare(b.obs_time ?? ""));
    if (frames.length) openFrames(frames, 0);
  };

  return (
    <>
      <div className={styles.runBar}>
        {active && job ? (
          <span className={styles.progress}>
            <span className={styles.progressBar}>
              <span style={{ width: `${job.total ? (100 * job.done) / job.total : 0}%` }} />
            </span>
            {jobLabel(job)}
          </span>
        ) : (
          <button type="button" className="button button-primary" onClick={run}>
            {stored ? <RotateCw size={13} strokeWidth={1.8} /> : <Play size={13} strokeWidth={1.8} />}
            {stored ? "Re-run analysis" : "Analyse this night"}
          </button>
        )}
        {job?.status === "failed" ? (
          <span>
            <StatusIcon level="bad" /> Last run failed: {job.error}
          </span>
        ) : null}
        {stored ? (
          <span className={styles.meta}>
            Analysed {relativeTime(new Date(stored.finished * 1000).toISOString())} in {stored.duration.toFixed(0)} s ·
            astro-eval {r?.astroEvalVersion} · {r?.measured} frames
            {r?.configPath ? ` · config ${r.configPath}` : " · default config"}
            {r?.errors.length ? ` · ${r.errors.length} unreadable` : ""}
          </span>
        ) : null}
      </div>

      {!r ? (
        <p className="muted">
          This night has not been analysed yet. A run measures every light and flat with astro-eval (roughly a minute
          per 150 frames) and keeps the results in the dashboard.
        </p>
      ) : (
        <>
          {analysis ? <Findings findings={analysis.findings} onPreview={openPaths} /> : null}

          <section className={styles.cards}>
            {lights.map((g) => {
              const reasons = Object.entries(g.summary.reasons).sort((a, b) => b[1] - a[1]);
              return (
                <div key={g.key} className={`framed ${styles.card}`}>
                  <Marks />
                  <div className={styles.cardHead}>
                    <FilterChip filter={filterLetter(g.filter)} long />
                    <span className="eyebrow">{g.target && g.key !== g.filter ? g.target : ""}</span>
                  </div>
                  <div className={styles.cardBig}>
                    {g.summary.kept}
                    <small>/ {g.summary.frames} kept</small>
                  </div>
                  <div className={styles.cardStats}>
                    <span>{hours(g.summary.keptSeconds)} usable</span>
                    <span>FWHM {num(g.summary.median.fwhm_median, 2)}″</span>
                    <span>{num(g.summary.median.n_stars)} stars</span>
                    <span>ecc {num(g.summary.median.eccentricity_median, 2)}</span>
                  </div>
                  {reasons.length ? (
                    <div className={styles.reasons}>
                      {reasons.slice(0, 5).map(([k, v]) => (
                        <span key={k}>
                          {reasonLabel(k)} <span className="num">×{v}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className={styles.reasons}>
                      <StatusIcon level="ok" size={13} /> nothing rejected
                    </div>
                  )}
                </div>
              );
            })}
          </section>

          {r.calibration?.rows.length ? (
            <section className={styles.block}>
              <div className={styles.blockHead}>
                <h2 className="section-title">Lights ↔ flats</h2>
                <span className="eyebrow">astro-eval calibration check</span>
              </div>
              {r.calibration.common.map((n) => (
                <p key={n.text} style={{ fontSize: 13.5, marginBottom: 8 }}>
                  <StatusIcon level={NOTE_LEVEL[n.level]} size={13} /> {n.text}
                </p>
              ))}
              {r.calibration.rows.map((row) => (
                <div key={row.light} className={styles.calRow}>
                  <StatusIcon level={CAL_LEVEL[row.status] ?? "info"} label={row.status} />
                  <span>
                    <FilterChip filter={filterLetter(row.filter)} long /> <span className="muted num">· {row.n}</span>
                    {row.light.includes(" · ") ? (
                      <span className="muted" style={{ display: "block", fontSize: 11.5 }}>
                        {row.light.split(" · ")[0]}
                      </span>
                    ) : null}
                  </span>
                  <span className="mono" style={{ fontSize: 12 }}>
                    {row.flat ?? "no flats"}
                  </span>
                  <ul className={styles.calIssues}>
                    {row.issues
                      .filter((i) => i.level !== "ok")
                      .map((i) => (
                        <li key={i.text}>
                          <StatusIcon level={NOTE_LEVEL[i.level]} size={12} />
                          <span>{i.text}</span>
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
            </section>
          ) : null}

          <section className={styles.block}>
            <Timeline groups={lights} onSelect={openFrames} events={analysis?.events ?? []} flags={flags} />
          </section>

          <section className={styles.block}>
            <FrameTable groups={lights} onSelect={openFrames} flags={flags} />
          </section>

          {r.flats.length ? (
            <section className={styles.block}>
              <div className={styles.blockHead}>
                <h2 className="section-title">Flats</h2>
                <span className="eyebrow">per set · master SNR = per-flat SNR × √n</span>
              </div>
              {r.flatMatch?.length ? (
                <div className={styles.flatMatch}>
                  <div className="eyebrow" style={{ marginBottom: 8 }}>
                    Do the flats correct these lights? (dust shadows and vignetting, experimental)
                  </div>
                  {r.flatMatch.map((m) => (
                    <div key={m.light} className={styles.flatMatchRow}>
                      <StatusIcon
                        level={m.verdict === "ok" ? "ok" : m.verdict === "unknown" ? "info" : "warn"}
                        label={m.verdict}
                        size={13}
                      />
                      <span>
                        <FilterChip filter={filterLetter(m.filter)} long />{" "}
                        <span className="muted mono" style={{ fontSize: 11 }}>
                          {m.light !== m.filter ? m.light.split(" · ")[0] + " · " : ""}
                          {m.flatSet}
                        </span>
                      </span>
                      <span className={styles.flatMatchNotes}>{m.notes.join(" ")}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className={styles.cards}>
                {r.flats.map((f) => (
                  <div key={f.key} className={`framed ${styles.flatCard}`}>
                    <Marks />
                    <div className={styles.cardHead}>
                      <span>
                        <StatusIcon
                          level={f.summary?.verdict === "good" ? "ok" : f.summary?.verdict === "warn" ? "warn" : "bad"}
                          label={f.summary?.verdict}
                        />{" "}
                        <FilterChip filter={filterLetter(f.filter)} long />
                      </span>
                      <span className="eyebrow">{f.key.split("· ").pop()}</span>
                    </div>
                    {f.summary ? (
                      <div className={styles.cardStats} style={{ marginTop: 8 }}>
                        <span>
                          {f.summary.kept}/{f.summary.n} kept
                        </span>
                        <span>level {f.summary.level != null ? `${Math.round(f.summary.level * 100)}%` : "—"}</span>
                        <span>SNR {num(f.summary.snr)}</span>
                        <span>master {num(f.summary.masterSnr)}</span>
                      </div>
                    ) : null}
                    <ul className={styles.flatNotes}>
                      {(f.summary?.notes ?? [])
                        .filter((n) => n.level !== "info")
                        .map((n) => (
                          <li key={n.text}>
                            <StatusIcon level={NOTE_LEVEL[n.level]} size={12} />
                            <span>{n.text}</span>
                          </li>
                        ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}

      {selected ? <PreviewModal items={selected.list} index={selected.index} onClose={() => setSelected(null)} /> : null}
    </>
  );
}
