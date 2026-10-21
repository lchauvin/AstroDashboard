"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, RotateCw } from "lucide-react";

import type { DiagDetail, DiagJob, LightFrame, LightGroupResult } from "@/lib/diagnostics";
import { FLAG_LABELS, decisionFor, filterLetter, overrideNote, reasonLabel } from "@/lib/diagnostics";
import { hours, num, relativeTime } from "@/lib/format";
import { FilterChip, Marks, StatusIcon } from "../ui";
import { Calibration, flatLevel } from "./Calibration";
import { Findings } from "./Findings";
import { FrameTable } from "./FrameTable";
import { PreviewModal } from "./PreviewModal";
import { byFilter, calibrationEntries, groupFindings, panelsOf, scopeFindings, worst } from "./scope";
import { Timeline } from "./Timeline";
import { jobLabel } from "./useJobs";
import styles from "./Diagnostics.module.css";

export type Section = "summary" | "night" | "frames" | "calibration";
const SECTIONS: { id: Section; label: string }[] = [
  { id: "summary", label: "Summary" },
  { id: "night", label: "Through the night" },
  { id: "frames", label: "Frames" },
  { id: "calibration", label: "Calibration" },
];

export function SessionDiagnostics({
  detail,
  initialSection,
  initialPanel,
  autoRun = false,
}: {
  detail: DiagDetail;
  initialSection?: string;
  initialPanel?: string;
  /** Start the analysis on arrival when the night has none yet ("Run diagnostic" from a target page). */
  autoRun?: boolean;
}) {
  const router = useRouter();
  const [job, setJob] = useState<DiagJob | null>(detail.job);
  const [selected, setSelected] = useState<{ paths: string[]; index: number } | null>(null);
  // The night as last saved: keep / reject decisions replace it without a page reload.
  const [current, setCurrent] = useState(detail);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>(SECTIONS.some((s) => s.id === initialSection) ? (initialSection as Section) : "summary");
  const [panelId, setPanelId] = useState<string | null>(initialPanel ?? null);
  const active = job && (job.status === "queued" || job.status === "running");
  const url = `/engine/api/diagnostics/${detail.revision.id}/${encodeURIComponent(detail.session.folder)}`;

  useEffect(() => setJob(detail.job), [detail.job]);
  useEffect(() => setCurrent(detail), [detail]);
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

  const autoRan = useRef(false);
  useEffect(() => {
    if (!autoRun || autoRan.current) return;
    autoRan.current = true;
    // Drop ?run=1 so a reload or back-navigation does not queue another run.
    const params = new URLSearchParams(window.location.search);
    params.delete("run");
    router.replace(`${window.location.pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
    if (!current.stored && !(job && ["queued", "running"].includes(job.status))) void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun]);

  const stored = current.stored;
  const r = stored?.result;
  const analysis = current.analysis;
  const flags = useMemo(() => analysis?.flags ?? {}, [analysis]);
  const allLights = useMemo(() => r?.lights ?? [], [r]);

  // Mosaic scope: one panel, or all of them.
  const panels = useMemo(() => panelsOf(allLights), [allLights]);
  const panel = panels.find((p) => p.label === panelId) ?? null;
  const lights = panel ? allLights.filter((g) => g.target === panel.id) : allLights;
  const panelOfPath = useMemo(() => {
    const m = new Map<string, string>();
    for (const g of allLights) for (const f of g.frames) m.set(f.path, panels.find((p) => p.id === g.target)?.label ?? "");
    return m;
  }, [allLights, panels]);

  const scope = useMemo(() => {
    const paths = panel ? new Set(lights.flatMap((g) => g.frames.map((f) => f.path))) : null;
    const filters = panel ? new Set(lights.map((g) => g.filter)) : null;
    const findings = groupFindings(scopeFindings(analysis?.findings ?? [], paths, filters));
    const keys = new Set(lights.map((g) => g.key));
    const cal = r ? calibrationEntries(r, keys, panels) : [];
    const flats = (r?.flats ?? []).filter((f) => !filters || filters.has(f.filter));
    return { findings, cal, flats };
  }, [panel, lights, analysis, r, panels]);

  const select = (s: Section, p: string | null) => {
    setSection(s);
    setPanelId(p);
    const q = new URLSearchParams();
    if (s !== "summary") q.set("tab", s);
    if (p) q.set("panel", p);
    window.history.replaceState(null, "", `${window.location.pathname}${q.size ? `?${q}` : ""}`);
  };

  const caption = (f: LightFrame) => {
    const found = (flags[f.path] ?? []).map((c) => FLAG_LABELS[c] ?? c);
    return [
      panels.length ? panelOfPath.get(f.path) : "",
      f.filter_name ?? "",
      f.rejected ? "rejected: " + f.reasons.map(reasonLabel).join(", ") : f.override ? `kept ${overrideNote(f)}` : "kept",
      `FWHM ${num(f.fwhm_median, 2)}″`,
      `${num(f.n_stars)} stars`,
      `score ${num(f.score, 2)}`,
      found.length ? `findings: ${found.join(", ")}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  };
  const frameByPath = useMemo(() => new Map(allLights.flatMap((g) => g.frames.map((f) => [f.path, f] as const))), [allLights]);
  const openFrames = (frames: LightFrame[], index: number) => setSelected({ paths: frames.map((f) => f.path), index });
  const previewItems = (selected?.paths ?? [])
    .map((p) => frameByPath.get(p))
    .filter((f): f is LightFrame => Boolean(f))
    .map((f) => ({ path: f.path, file: f.file, caption: caption(f), rejected: f.rejected }));

  async function decide(f: LightFrame, reject: boolean) {
    if (deciding || f.rejected === reject) return;
    setDeciding(f.path);
    setDecideError(null);
    try {
      const res = await fetch(`${url}/frames`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: f.path, decision: decisionFor(f, reject) }),
      });
      if (!res.ok) throw new Error(((await res.json().catch(() => null)) as { detail?: string } | null)?.detail ?? res.statusText);
      setCurrent((await res.json()) as DiagDetail);
    } catch (e) {
      setDecideError(`${f.file}: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeciding(null);
    }
  }
  const decidePath = (path: string, reject: boolean) => {
    const f = frameByPath.get(path);
    if (f) void decide(f, reject);
  };
  const openPaths = (paths: string[]) => {
    const wanted = new Set(paths);
    const frames = allLights
      .flatMap((g) => g.frames)
      .filter((f) => wanted.has(f.path))
      .sort((a, b) => (a.obs_time ?? "").localeCompare(b.obs_time ?? ""));
    if (frames.length) openFrames(frames, 0);
  };

  const warnings = scope.findings.filter((g) => g.level !== "info");
  const calLevel = worst([...scope.cal.map((e) => e.level), ...scope.flats.map((f) => flatLevel(f))]);
  const frameCount = lights.reduce((s, g) => s + g.summary.frames, 0);

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
          <button type="button" className="button" onClick={run}>
            {stored ? <RotateCw size={13} strokeWidth={1.8} /> : <Play size={13} strokeWidth={1.8} />}
            {stored ? "Re-run analysis" : "Analyse this night"}
          </button>
        )}
        {job?.status === "failed" ? (
          <span>
            <StatusIcon level="bad" /> Last run failed: {job.error}
          </span>
        ) : null}
        {decideError ? (
          <span>
            <StatusIcon level="bad" /> Could not save the decision — {decideError}
          </span>
        ) : null}
        {stored ? (
          <span
            className={styles.meta}
            title={`astro-eval ${r?.astroEvalVersion} · ${r?.configPath ? `config ${r.configPath}` : "default config"} · ${stored.duration.toFixed(0)} s`}
          >
            Analysed {relativeTime(new Date(stored.finished * 1000).toISOString())} · {r?.measured} frames measured
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
          {panels.length ? (
            <div className={styles.panelBar} role="group" aria-label="Mosaic panel">
              <span className="eyebrow">Mosaic</span>
              {[null, ...panels].map((p) => {
                const id = p?.label ?? null;
                const on = (panel?.label ?? null) === id;
                return (
                  <button
                    key={id ?? "all"}
                    type="button"
                    className={on ? `${styles.panelBtn} ${styles.panelBtnActive}` : styles.panelBtn}
                    aria-pressed={on}
                    onClick={() => select(section, id)}
                  >
                    {p ? p.label : `All ${panels.length} panels`}
                  </button>
                );
              })}
            </div>
          ) : null}

          <nav className={styles.sectionTabs} role="tablist" aria-label="Sections">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                role="tab"
                aria-selected={section === s.id}
                className={section === s.id ? `${styles.sectionTab} ${styles.sectionTabActive}` : styles.sectionTab}
                onClick={() => select(s.id, panel?.label ?? null)}
              >
                {s.id === "summary" && warnings.length ? <StatusIcon level={worst(warnings.map((g) => g.level))} size={12} /> : null}
                {s.id === "calibration" && (scope.cal.length || scope.flats.length) ? <StatusIcon level={calLevel} size={12} /> : null}
                {s.label}
                {s.id === "frames" ? <span className={styles.tabCount}>{frameCount}</span> : null}
                {s.id === "summary" && warnings.length ? <span className={styles.tabCount}>{warnings.length}</span> : null}
              </button>
            ))}
          </nav>

          {section === "summary" ? (
            <>
              {analysis ? <Findings groups={scope.findings} onPreview={openPaths} /> : null}
              {panels.length && !panel ? (
                <PanelMatrix
                  panels={panels}
                  lights={allLights}
                  onOpen={(label) => select("summary", label)}
                />
              ) : null}
              <section className={styles.cards}>
                {byFilter(lights).map((s) => (
                  <div key={s.filter} className={`framed ${styles.card}`}>
                    <Marks />
                    <div className={styles.cardHead}>
                      <FilterChip filter={filterLetter(s.filter)} long />
                      <span className={styles.cardHours}>{hours(s.keptSeconds)} usable</span>
                    </div>
                    <div className={styles.cardBig}>
                      {s.kept}
                      <small>/ {s.frames} kept</small>
                    </div>
                    <div className={styles.cardStats}>
                      <span>FWHM {num(s.fwhm, 2)}″</span>
                      <span>{num(s.stars)} stars</span>
                      <span>ecc {num(s.ecc, 2)}</span>
                    </div>
                    <div className={styles.reasons}>
                      {s.reasons.length ? (
                        s.reasons.slice(0, 3).map(([k, v]) => (
                          <span key={k}>
                            {reasonLabel(k)} <span className="num">×{v}</span>
                          </span>
                        ))
                      ) : (
                        <span>
                          <StatusIcon level="ok" size={12} /> nothing rejected
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </section>
            </>
          ) : null}

          {section === "night" ? (
            <section className={styles.block}>
              <Timeline groups={lights} onSelect={openFrames} events={analysis?.events ?? []} flags={flags} />
            </section>
          ) : null}

          {section === "frames" ? (
            <section className={styles.block}>
              <FrameTable
                groups={lights}
                onSelect={openFrames}
                flags={flags}
                panelOf={panels.length && !panel ? (p) => panelOfPath.get(p) : undefined}
                onDecide={decide}
                deciding={deciding}
              />
            </section>
          ) : null}

          {section === "calibration" ? <Calibration result={r} entries={scope.cal} flats={scope.flats} mosaic={panels.length > 0 && !panel} /> : null}
        </>
      )}

      {selected && previewItems.length ? (
        <PreviewModal
          items={previewItems}
          index={selected.index}
          onClose={() => setSelected(null)}
          onDecide={decidePath}
          deciding={deciding}
        />
      ) : null}
    </>
  );
}

/** Mosaic at a glance: kept / measured per panel and filter. */
function PanelMatrix({
  panels,
  lights,
  onOpen,
}: {
  panels: { id: string; label: string }[];
  lights: LightGroupResult[];
  onOpen: (label: string) => void;
}) {
  const filters = [...new Set(lights.map((g) => g.filter))];
  return (
    <section className={styles.block}>
      <div className={styles.blockHead}>
        <h2 className="section-title">Panels</h2>
        <span className="eyebrow">kept / measured</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className={`table ${styles.matrix}`}>
          <thead>
            <tr>
              <th>Panel</th>
              {filters.map((f) => (
                <th key={f} className="num">
                  <FilterChip filter={filterLetter(f)} />
                </th>
              ))}
              <th className="num">Usable</th>
            </tr>
          </thead>
          <tbody>
            {panels.map((p) => {
              const gs = lights.filter((g) => g.target === p.id);
              return (
                <tr key={p.id}>
                  <td>
                    <button type="button" className={styles.matrixLink} onClick={() => onOpen(p.label)}>
                      {p.label}
                    </button>
                  </td>
                  {filters.map((f) => {
                    const g = gs.filter((x) => x.filter === f);
                    const frames = g.reduce((s, x) => s + x.summary.frames, 0);
                    const kept = g.reduce((s, x) => s + x.summary.kept, 0);
                    return (
                      <td key={f} className={`num ${frames && kept < frames ? styles.matrixLoss : ""}`}>
                        {frames ? `${kept}/${frames}` : "—"}
                      </td>
                    );
                  })}
                  <td className="num">{hours(gs.reduce((s, x) => s + x.summary.keptSeconds, 0))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
