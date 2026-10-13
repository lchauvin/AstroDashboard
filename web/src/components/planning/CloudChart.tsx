"use client";

import { useState } from "react";

import styles from "./charts.module.css";

export type ChartSlot = {
  /** Centre of the slot, ms. */
  t: number;
  /** Hours the value covers (1, or 3 for the extended forecast). */
  step: number;
  cloud: number | null;
  gfs: number | null;
  icon: number | null;
  transparency: number | null;
  seeing: number | null;
  extended: boolean;
};

const W = 760;
const H = 230;
const M = { l: 38, r: 52, t: 22, b: 44 };
const LANE = 10; // the moon lane under the plot
const CLEAR = 30;

const time = (ms: number) => new Date(ms).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });

/**
 * Cloud cover through one night's darkness: Astrospheric (accent), GFS and ICON
 * (dashed and dotted, the second and third opinions), the 30 % "clear" line, the
 * longest clear stretch shaded, and when the moon is up (lane under the plot).
 * Each value is drawn as a step across the slot it covers. Single axis, percent.
 */
export function CloudChart({
  dusk,
  dawn,
  slots,
  moonUp,
  clearRun,
}: {
  dusk: number;
  dawn: number;
  slots: ChartSlot[];
  moonUp: { start: number; end: number }[];
  clearRun: { start: number; end: number } | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!slots.length || dawn <= dusk) return null;
  const plotB = H - M.b - LANE - 6;
  const x = (t: number) => M.l + ((Math.min(dawn, Math.max(dusk, t)) - dusk) / (dawn - dusk)) * (W - M.l - M.r);
  const y = (v: number) => M.t + ((100 - Math.max(0, Math.min(100, v))) / 100) * (plotB - M.t);
  const r = (v: number) => Math.round(v * 10) / 10;
  const half = (s: ChartSlot) => (s.step * 3_600_000) / 2;

  // A step line: flat across each slot, broken where a model has no value.
  const steps = (get: (s: ChartSlot) => number | null) => {
    let d = "";
    let prevEnd: number | null = null;
    for (const s of slots) {
      const v = get(s);
      if (v == null) {
        prevEnd = null;
        continue;
      }
      const a = r(x(s.t - half(s)));
      const b = r(x(s.t + half(s)));
      d += prevEnd != null && Math.abs(prevEnd - a) < 1 ? ` L${a},${r(y(v))} L${b},${r(y(v))}` : ` M${a},${r(y(v))} L${b},${r(y(v))}`;
      prevEnd = b;
    }
    return d.trim();
  };
  const series = [
    { key: "cloud", name: "Astrospheric", get: (s: ChartSlot) => s.cloud, cls: styles.cloudMain },
    { key: "gfs", name: "GFS", get: (s: ChartSlot) => s.gfs, cls: styles.cloudGfs },
    { key: "icon", name: "ICON", get: (s: ChartSlot) => s.icon, cls: styles.cloudIcon },
  ].filter((se) => slots.some((s) => se.get(s) != null));

  // Direct labels at the right end, nudged apart so they never overlap.
  const ends = series
    .map((se) => {
      const last = [...slots].reverse().find((s) => se.get(s) != null);
      return { name: se.name, y: last ? y(se.get(last)!) : 0 };
    })
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 12);

  const ticks: number[] = [];
  const first = new Date(dusk);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3_600_000; t < dawn; t += 3_600_000) ticks.push(t);
  const hs = hover != null ? slots[hover] : null;
  const moonAt = (t: number) => moonUp.some((m) => t >= m.start && t <= m.end);
  const fmt = (v: number | null, unit = " %") => (v == null ? "—" : `${Math.round(v)}${unit}`);

  return (
    <figure className={styles.figure}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.svg}
        role="img"
        aria-label="Cloud cover through the night"
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const t = dusk + ((px - M.l) / (W - M.l - M.r)) * (dawn - dusk);
          let best = 0;
          for (let i = 1; i < slots.length; i++) if (Math.abs(slots[i].t - t) < Math.abs(slots[best].t - t)) best = i;
          setHover(px >= M.l && px <= W - M.r ? best : null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {clearRun ? (
          <g>
            <rect x={x(clearRun.start)} y={M.t} width={Math.max(0, x(clearRun.end) - x(clearRun.start))} height={plotB - M.t} className={styles.usable} />
            <text x={x(clearRun.start) + 2} y={M.t - 8} className={styles.axis}>
              longest clear {((clearRun.end - clearRun.start) / 3_600_000).toFixed(1)} h
            </text>
          </g>
        ) : null}
        {[0, 50, 100].map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={styles.grid} />
            <text x={M.l - 6} y={y(v)} className={styles.axis} textAnchor="end" dominantBaseline="middle">
              {v}%
            </text>
          </g>
        ))}
        <line x1={M.l} x2={W - M.r} y1={y(CLEAR)} y2={y(CLEAR)} className={styles.floor} />
        <text x={M.l - 6} y={y(CLEAR)} className={styles.axis} textAnchor="end" dominantBaseline="middle">
          {CLEAR}%
        </text>
        {series.map((se) => (
          <path key={se.key} d={steps(se.get)} className={se.cls} />
        ))}
        {ends.map((e) => (
          <text key={e.name} x={W - M.r + 6} y={e.y} className={styles.axis} dominantBaseline="middle">
            {e.name === "Astrospheric" ? "Astro" : e.name}
          </text>
        ))}
        {/* Moon lane */}
        <text x={M.l - 6} y={plotB + 6 + LANE / 2} className={styles.axis} textAnchor="end" dominantBaseline="middle">
          moon
        </text>
        <rect x={M.l} y={plotB + 6} width={W - M.l - M.r} height={LANE} className={styles.laneTrack} />
        {moonUp.map((m) => (
          <rect key={m.start} x={x(m.start)} y={plotB + 6} width={Math.max(0, x(m.end) - x(m.start))} height={LANE} className={styles.laneMoon} />
        ))}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 12} className={styles.axis} textAnchor="middle">
            {time(t).slice(0, 2)}h
          </text>
        ))}
        {hs ? <line x1={x(hs.t)} x2={x(hs.t)} y1={M.t} y2={plotB + 6 + LANE} className={styles.crosshair} /> : null}
      </svg>
      <figcaption className={styles.legend}>
        {series.map((se) => (
          <span key={se.key}>
            <i className={se.key === "cloud" ? styles.keyTarget : se.key === "gfs" ? styles.keyGfs : styles.keyIcon} /> {se.name}
          </span>
        ))}
        <span>
          <i className={styles.keyFloor} /> Clear below {CLEAR} %
        </span>
        <span>
          <i className={styles.keyUsable} /> Longest clear stretch
        </span>
        <span className={styles.readout} aria-live="polite">
          {hs
            ? `${time(hs.t)} · cloud ${fmt(hs.cloud)}${hs.gfs != null ? ` · GFS ${fmt(hs.gfs)}` : ""}${hs.icon != null ? ` · ICON ${fmt(hs.icon)}` : ""}${hs.transparency != null ? ` · transp ${fmt(hs.transparency, "")}` : ""}${hs.seeing != null ? ` · seeing ${fmt(hs.seeing, "/5")}` : ""} · moon ${moonAt(hs.t) ? "up" : "down"}${hs.extended ? " · extended (3 h)" : ""}`
            : "Hover the chart for values"}
        </span>
      </figcaption>
    </figure>
  );
}
