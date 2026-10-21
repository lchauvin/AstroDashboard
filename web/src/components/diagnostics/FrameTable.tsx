"use client";

import { useMemo, useState } from "react";

import type { LightFrame, LightGroupResult } from "@/lib/diagnostics";
import { FLAG_LABELS, filterLetter, overrideNote, reasonLabel } from "@/lib/diagnostics";
import { num } from "@/lib/format";
import type { FrameFraming } from "@/lib/framing";
import { FilterChip, StatusIcon } from "../ui";
import styles from "./Diagnostics.module.css";

type Col = { key: keyof LightFrame | "time"; label: string; digits?: number; title?: string };
const COLS: Col[] = [
  { key: "time", label: "Time" },
  { key: "score", label: "Score", digits: 2 },
  { key: "fwhm_median", label: "FWHM″", digits: 2 },
  { key: "eccentricity_median", label: "Ecc", digits: 2 },
  { key: "n_stars", label: "Stars", digits: 0 },
  { key: "background_median", label: "Bg", digits: 0, title: "Background median (ADU)" },
  { key: "background_gradient", label: "Grad σ", digits: 1, title: "Background gradient in noise σ" },
  { key: "transparency", label: "Transp", digits: 2, title: "Stellar flux vs session median (1.0 = typical)" },
  { key: "region_transparency_min", label: "Worst cell", digits: 2, title: "Worst 1/16 of the frame vs the clear part — obstruction" },
  { key: "altitude_deg", label: "Alt°", digits: 0 },
  { key: "sunAlt", label: "Sun°", digits: 0 },
];

const timeOf = (f: LightFrame) => (f.obs_time ? Date.parse(f.obs_time.endsWith("Z") ? f.obs_time : f.obs_time + "Z") : NaN);

export function FrameTable({
  groups,
  onSelect,
  flags = {},
  panelOf,
  onDecide,
  deciding,
  framing,
}: {
  groups: LightGroupResult[];
  onSelect: (frames: LightFrame[], index: number) => void;
  flags?: Record<string, string[]>;
  /** Mosaics: the panel label of a frame path (adds a Panel column). */
  panelOf?: (path: string) => string | undefined;
  /** Keep or reject a frame by hand (adds a button to every row). */
  onDecide?: (frame: LightFrame, reject: boolean) => void;
  /** Path of the frame whose decision is being saved. */
  deciding?: string | null;
  /** Each frame against the previous night's framing (adds a Framing column). */
  framing?: Record<string, FrameFraming>;
}) {
  const [tab, setTab] = useState<string>("all");
  const [sort, setSort] = useState<{ key: Col["key"] | "framing"; dir: 1 | -1 }>({ key: "time", dir: 1 });
  const [onlyRejected, setOnlyRejected] = useState(false);

  const filters = [...new Set(groups.map((g) => g.filter))];
  const current = tab === "all" || filters.includes(tab) ? tab : "all";
  const inTab = (g: LightGroupResult) => current === "all" || g.filter === current;

  const rows = useMemo(() => {
    const src = groups.filter(inTab).flatMap((g) => g.frames);
    const filtered = onlyRejected ? src.filter((f) => f.rejected) : src;
    const val = (f: LightFrame) =>
      sort.key === "time" ? timeOf(f) : sort.key === "framing" ? (framing?.[f.path]?.cornerPx ?? null) : (f[sort.key] as number | null);
    return [...filtered].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null || Number.isNaN(va)) return 1;
      if (vb == null || Number.isNaN(vb)) return -1;
      return (va - vb) * sort.dir;
    });
  }, [groups, current, sort, onlyRejected, framing]);

  const rejectedCount = groups.filter(inTab).flatMap((g) => g.frames).filter((f) => f.rejected).length;

  return (
    <div>
      <div className={styles.blockHead}>
        <span className={styles.tabs} role="group" aria-label="Filter">
          {["all", ...filters].map((t) => (
            <button
              key={t}
              type="button"
              className={current === t ? `${styles.tab} ${styles.tabActive}` : styles.tab}
              aria-pressed={current === t}
              onClick={() => setTab(t)}
            >
              {t === "all" ? "All filters" : <FilterChip filter={filterLetter(t)} />}
            </button>
          ))}
        </span>
        <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 13.5 }}>
          <input type="checkbox" checked={onlyRejected} onChange={(e) => setOnlyRejected(e.target.checked)} />
          Only rejected ({rejectedCount})
        </label>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className={`table ${styles.frameTable}`}>
          <thead>
            <tr>
              {panelOf ? <th>Panel</th> : null}
              <th>Filter</th>
              <th>Status</th>
              {onDecide ? <th aria-label="Keep or reject" /> : null}
              <th>Findings</th>
              {framing ? (
                <th
                  title="Largest corner shift from the previous night's framing (px): within both nights' spread, up to twice that, beyond"
                  aria-sort={sort.key === "framing" ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
                  onClick={() => setSort((s) => ({ key: "framing", dir: s.key === "framing" ? ((-s.dir) as 1 | -1) : 1 }))}
                  style={{ textAlign: "right" }}
                >
                  Framing{sort.key === "framing" ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </th>
              ) : null}
              {COLS.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
                  onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? ((-s.dir) as 1 | -1) : 1 }))}
                  style={{ textAlign: c.key === "time" ? "left" : "right" }}
                >
                  {c.label}
                  {sort.key === c.key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((f, i) => (
              <tr key={f.path} className={f.rejected ? styles.rejectedRow : undefined} onClick={() => onSelect(rows, i)}>
                {panelOf ? <td>{panelOf(f.path)}</td> : null}
                <td>
                  <FilterChip filter={filterLetter(f.filter_name)} />
                </td>
                <td>
                  {f.rejected ? (
                    <span style={{ display: "inline-flex", gap: 6, alignItems: "flex-start" }}>
                      <StatusIcon level="bad" size={13} label="Rejected" />
                      <span className={styles.reasonList}>{f.reasons.map(reasonLabel).join(", ")}</span>
                    </span>
                  ) : (
                    <span style={{ display: "inline-flex", gap: 6 }}>
                      <StatusIcon level="ok" size={13} label="Kept" />
                      {f.override ? (
                        <span className={styles.reasonList}>{overrideNote(f)}</span>
                      ) : f.n_trails ? (
                        <span className={styles.reasonList}>{f.trail_type} trail</span>
                      ) : null}
                    </span>
                  )}
                </td>
                {onDecide ? (
                  <td>
                    <button
                      type="button"
                      className={styles.decide}
                      disabled={deciding === f.path}
                      title={f.rejected ? "Keep this frame anyway" : "Reject this frame"}
                      onClick={(e) => {
                        e.stopPropagation();
                        onDecide(f, !f.rejected);
                      }}
                    >
                      {f.rejected ? "Keep" : "Reject"}
                    </button>
                  </td>
                ) : null}
                <td>
                  <span className={styles.flagBadges}>
                    {(flags[f.path] ?? []).map((code) => (
                      <span key={code} className={styles.flagBadge}>
                        {FLAG_LABELS[code] ?? code}
                      </span>
                    ))}
                  </span>
                </td>
                {framing ? (
                  <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <FramingValue x={framing[f.path]} />
                  </td>
                ) : null}
                {COLS.map((c) => (
                  <td key={c.key} className="num" style={{ textAlign: c.key === "time" ? "left" : "right" }}>
                    {c.key === "time"
                      ? Number.isFinite(timeOf(f))
                        ? new Date(timeOf(f)).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false })
                        : "—"
                      : num(f[c.key] as number | null, c.digits)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A frame against the previous night's framing: px, with a mark when beyond both nights' spread. */
function FramingValue({ x }: { x: FrameFraming | undefined }) {
  if (x?.cornerPx == null) return <>—</>;
  return (
    <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }} title={x.outlier ? "Framed differently from the rest of this night" : undefined}>
      {x.level && x.level !== "ok" ? <StatusIcon level={x.level} size={12} /> : null}
      {Math.round(x.cornerPx)}
      {x.outlier ? "*" : ""}
    </span>
  );
}
