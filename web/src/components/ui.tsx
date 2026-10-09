import { AlertTriangle, CheckCircle2, CircleSlash, Info, XOctagon } from "lucide-react";

import { filterColor, filterLabel } from "@/lib/format";
import type { Level } from "@/lib/types";
import styles from "./ui.module.css";

/** The `+` registration marks at the corners of a framed object (parent must be `position: relative`). */
export function Marks() {
  return (
    <>
      <span className="mk mk-tl" aria-hidden="true">+</span>
      <span className="mk mk-tr" aria-hidden="true">+</span>
      <span className="mk mk-bl" aria-hidden="true">+</span>
      <span className="mk mk-br" aria-hidden="true">+</span>
    </>
  );
}

const LEVEL_ICON = { ok: CheckCircle2, info: Info, warn: AlertTriangle, bad: XOctagon } as const;
const LEVEL_COLOR = {
  ok: "var(--status-good)",
  info: "var(--status-info)",
  warn: "var(--status-warn)",
  bad: "var(--status-bad)",
} as const;
const LEVEL_TEXT = { ok: "OK", info: "Note", warn: "Warning", bad: "Problem" } as const;

/** Status is never colour alone: icon + (optionally visible) label. */
export function StatusIcon({ level, size = 15, label }: { level: Level; size?: number; label?: string }) {
  const Icon = LEVEL_ICON[level];
  return (
    <span className={styles.status} title={label ?? LEVEL_TEXT[level]}>
      <Icon size={size} strokeWidth={1.8} color={LEVEL_COLOR[level]} aria-hidden="true" />
      <span className="sr-only">{label ?? LEVEL_TEXT[level]}</span>
    </span>
  );
}

export function NoneIcon({ size = 14, label }: { size?: number; label: string }) {
  return (
    <span className={styles.status} title={label}>
      <CircleSlash size={size} strokeWidth={1.6} color="var(--text-muted)" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** Filter identity: a colour square that always travels with its letter. */
export function FilterChip({ filter, long = false }: { filter: string | null; long?: boolean }) {
  return (
    <span className={styles.filterChip}>
      <span className={styles.filterSwatch} style={{ background: filterColor(filter) }} aria-hidden="true" />
      <span>{long ? filterLabel(filter) : filter ?? "?"}</span>
    </span>
  );
}

/** Hover / focus tooltip. Content is plain markup; the trigger is keyboard-focusable. */
export function Tip({ children, tip, block = false }: { children: React.ReactNode; tip: React.ReactNode; block?: boolean }) {
  return (
    <span className={block ? `${styles.tip} ${styles.tipBlock}` : styles.tip} tabIndex={0}>
      {children}
      <span className={styles.tipBox} role="tooltip">
        {tip}
      </span>
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "accent" | "muted" }) {
  return <span className={`${styles.badge} ${styles[`badge_${tone}`]}`}>{children}</span>;
}

export function StatTile({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className={`framed ${styles.stat}`}>
      <Marks />
      <div className="eyebrow">{label}</div>
      <div className={styles.statValue}>{value}</div>
      {sub ? <div className={styles.statSub}>{sub}</div> : null}
    </div>
  );
}
