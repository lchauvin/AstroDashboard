"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";

import { FILTER_ORDER, hours, shortDate, stageLabel } from "@/lib/format";
import type { RevisionSummary } from "@/lib/types";
import { FilterBar, FilterLegend } from "./FilterBar";
import { Badge, StatusIcon } from "./ui";
import styles from "./TargetsTable.module.css";

export interface TargetRow extends RevisionSummary {
  catalogId: string;
  name: string;
  revisionCount: number;
}

type Preset = "all" | "sho" | "lrgb" | "flats" | "acquiring";
type Sort = "name" | "hours" | "last";

const PRESETS: { id: Preset; label: string }[] = [
  { id: "all", label: "All" },
  { id: "sho", label: "Narrowband" },
  { id: "lrgb", label: "Broadband" },
  { id: "flats", label: "Needs flats" },
  { id: "acquiring", label: "Acquiring" },
];

const has = (r: TargetRow, f: string) => r.filters.some((x) => x.filter === f && x.seconds > 0);

function matches(r: TargetRow, preset: Preset): boolean {
  switch (preset) {
    case "sho":
      return ["H", "O", "S"].filter((f) => has(r, f)).length >= 2;
    case "lrgb":
      return ["R", "G", "B"].every((f) => has(r, f)) || has(r, "L");
    case "flats":
      return r.needsFlats;
    case "acquiring":
      return r.stage.value === "acquiring";
    default:
      return true;
  }
}

export function TargetsTable({ rows }: { rows: TargetRow[] }) {
  const [query, setQuery] = useState("");
  const [preset, setPreset] = useState<Preset>("all");
  const [sort, setSort] = useState<Sort>("name");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = rows.filter(
      (r) => matches(r, preset) && (!q || `${r.catalogId} ${r.name} ${r.folder}`.toLowerCase().includes(q)),
    );
    out.sort((a, b) => {
      if (sort === "hours") return b.totals.seconds - a.totals.seconds;
      if (sort === "last") return (b.totals.last ?? "").localeCompare(a.totals.last ?? "");
      return a.catalogId.localeCompare(b.catalogId, "en", { numeric: true }) || a.revision - b.revision;
    });
    return out;
  }, [rows, query, preset, sort]);

  // Scale bars to the largest target overall so rows stay comparable across filtering.
  const maxSeconds = Math.max(1, ...rows.map((r) => r.totals.seconds));
  const presentFilters = FILTER_ORDER.filter((f) => rows.some((r) => has(r, f)));

  return (
    <section>
      {/* Password-manager extensions (Proton Pass) tag the search box's container before hydration. */}
      <div className={styles.controls} suppressHydrationWarning>
        <label className={styles.search}>
          <Search size={14} strokeWidth={1.8} aria-hidden="true" />
          <input
            type="search"
            placeholder="Search targets"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search targets"
          />
        </label>
        <div className={styles.presets} role="group" aria-label="Show">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={preset === p.id ? `${styles.preset} ${styles.presetActive}` : styles.preset}
              aria-pressed={preset === p.id}
              onClick={() => setPreset(p.id)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className={styles.sort}>
          <span className="eyebrow">Sort</span>
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="name">Name</option>
            <option value="hours">Integration</option>
            <option value="last">Last session</option>
          </select>
        </label>
      </div>

      <div className={styles.legendRow}>
        <FilterLegend filters={presentFilters} />
      </div>

      <div className={styles.scroller}>
        <table className="table">
          <thead>
            <tr>
              <th aria-label="Preview" />
              <th>Target</th>
              <th>Status</th>
              <th style={{ textAlign: "right" }}>Nights</th>
              <th>Last session</th>
              <th style={{ textAlign: "right" }}>Integration</th>
              <th style={{ width: "30%" }}>Per filter</th>
              <th>Checks</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.id}>
                <td className={styles.thumbCell}>
                  <Link href={`/library/${r.id}`} tabIndex={-1} aria-hidden="true">
                    {r.hasPreview ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img className={styles.thumb} src={`/engine/api/revisions/${r.id}/preview.jpg`} alt="" loading="lazy" />
                    ) : (
                      <span className={`${styles.thumb} ${styles.thumbEmpty}`} />
                    )}
                  </Link>
                </td>
                <td>
                  <Link href={`/library/${r.id}`} className={styles.name}>
                    {r.catalogId}
                  </Link>
                  <div className={styles.sub}>
                    {r.name}
                    {r.revisionCount > 1 ? <Badge tone="accent">R{r.revision}</Badge> : null}
                    {r.panels.length ? <Badge tone="muted">{r.panels.length}-panel mosaic</Badge> : null}
                  </div>
                </td>
                <td>
                  <Badge tone={r.stage.value === "acquiring" ? "accent" : "neutral"}>{stageLabel(r.stage.value)}</Badge>
                </td>
                <td className="num" style={{ textAlign: "right" }}>
                  {r.totals.nights}
                </td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>{shortDate(r.totals.last)}</td>
                <td className={`num ${styles.hours}`}>{hours(r.totals.seconds)}</td>
                <td>
                  <FilterBar filters={r.filters} maxSeconds={maxSeconds} />
                </td>
                <td>
                  <span className={styles.issues}>
                    {r.issues.bad ? (
                      <span>
                        <StatusIcon level="bad" label={`${r.issues.bad} problem${r.issues.bad > 1 ? "s" : ""}`} />
                        <span className="num">{r.issues.bad}</span>
                      </span>
                    ) : null}
                    {r.issues.warn ? (
                      <span>
                        <StatusIcon level="warn" label={`${r.issues.warn} warning${r.issues.warn > 1 ? "s" : ""}`} />
                        <span className="num">{r.issues.warn}</span>
                      </span>
                    ) : null}
                    {!r.issues.bad && !r.issues.warn ? <StatusIcon level="ok" label="Ready to process" /> : null}
                  </span>
                </td>
              </tr>
            ))}
            {!visible.length ? (
              <tr>
                <td colSpan={8} className="muted" style={{ padding: "28px 10px" }}>
                  No target matches.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}
