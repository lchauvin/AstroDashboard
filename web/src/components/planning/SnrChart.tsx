"use client";

import { useState } from "react";

import { filterColor } from "@/lib/format";
import styles from "./charts.module.css";

export type SnrSeries = {
  letter: string;
  name: string;
  /** SNR² per hour under the reference sky: SNR(h) = √(rate·h). */
  rate: number;
  /** Good hours already collected (hollow marker), if any. */
  haveHours?: number;
  /** Goal (filled marker), if any. */
  goalHours?: number | null;
  goalSnr?: number | null;
};

const W = 760;
const H = 280;
const M = { l: 40, r: 56, t: 14, b: 28 };

/**
 * SNR vs integration per filter, one line per filter in the validated filter
 * palette, direct-labelled at the line ends and legend below. At a fixed sub
 * length the curve is exactly √T (read noise lowers the coefficient, it does
 * not bend the curve), so these are √ curves by construction.
 */
export function SnrChart({ series, maxHours }: { series: SnrSeries[]; maxHours?: number }) {
  const [hoverH, setHoverH] = useState<number | null>(null);
  const shown = series.filter((s) => s.rate > 0);
  if (!shown.length) return <p className="muted">No modelled signal in any filter.</p>;
  const goalMax = Math.max(0, ...shown.map((s) => (s.goalHours && Number.isFinite(s.goalHours) ? s.goalHours : 0)), ...shown.map((s) => s.haveHours ?? 0));
  const xMax = maxHours ?? Math.min(200, Math.max(20, Math.ceil((goalMax * 1.15) / 5) * 5));
  const snrAt = (s: SnrSeries, h: number) => Math.sqrt(s.rate * h);
  const yMaxRaw = Math.max(...shown.map((s) => snrAt(s, xMax)), ...shown.map((s) => s.goalSnr ?? 0));
  const step = yMaxRaw > 200 ? 50 : yMaxRaw > 80 ? 20 : yMaxRaw > 40 ? 10 : 5;
  const yMax = Math.ceil(yMaxRaw / step) * step || step;
  const x = (h: number) => M.l + (h / xMax) * (W - M.l - M.r);
  const y = (v: number) => M.t + (1 - Math.min(v, yMax) / yMax) * (H - M.t - M.b);
  const r = (v: number) => Math.round(v * 10) / 10;
  const path = (s: SnrSeries) =>
    Array.from({ length: 81 }, (_, i) => (xMax * i) / 80)
      .map((h, i) => `${i ? "L" : "M"}${r(x(h))},${r(y(snrAt(s, h)))}`)
      .join(" ");
  const xticks = Array.from({ length: 6 }, (_, i) => Math.round((xMax * i) / 5));
  const yticks = Array.from({ length: Math.floor(yMax / step) + 1 }, (_, i) => i * step);

  // Direct labels at the right end, nudged apart so they never overlap.
  const labels = shown
    .map((s) => ({ s, y: y(snrAt(s, xMax)) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 12) labels[i].y = labels[i - 1].y + 12;

  return (
    <figure className={styles.figure}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.svg}
        role="img"
        aria-label="SNR against integration hours per filter"
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const h = ((px - M.l) / (W - M.l - M.r)) * xMax;
          setHoverH(h >= 0 && h <= xMax ? h : null);
        }}
        onMouseLeave={() => setHoverH(null)}
      >
        {yticks.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={styles.grid} />
            <text x={M.l - 6} y={y(v)} className={styles.axis} textAnchor="end" dominantBaseline="middle">
              {v}
            </text>
          </g>
        ))}
        {xticks.map((h) => (
          <text key={h} x={x(h)} y={H - 8} className={styles.axis} textAnchor="middle">
            {h} h
          </text>
        ))}
        {shown.map((s) => (
          <path key={s.letter} d={path(s)} className={styles.seriesLine} style={{ stroke: filterColor(s.letter) }} />
        ))}
        {shown.map((s) =>
          s.haveHours ? (
            <circle key={`h${s.letter}`} cx={x(Math.min(s.haveHours, xMax))} cy={y(snrAt(s, s.haveHours))} r={4.5} className={styles.nowDot} style={{ stroke: filterColor(s.letter) }}>
              <title>{`${s.name}: ${s.haveHours.toFixed(1)} h collected → SNR ${snrAt(s, s.haveHours).toFixed(1)}`}</title>
            </circle>
          ) : null,
        )}
        {shown.map((s) =>
          s.goalHours && Number.isFinite(s.goalHours) && s.goalHours <= xMax ? (
            <rect key={`g${s.letter}`} x={x(s.goalHours) - 4.5} y={y(snrAt(s, s.goalHours)) - 4.5} width={9} height={9} className={styles.goalDot} style={{ fill: filterColor(s.letter) }}>
              <title>{`${s.name} goal: ${s.goalHours.toFixed(1)} h → SNR ${snrAt(s, s.goalHours).toFixed(1)}`}</title>
            </rect>
          ) : null,
        )}
        {labels.map(({ s, y: ly }) => (
          <text key={`l${s.letter}`} x={W - M.r + 6} y={ly} className={styles.seriesLabel} dominantBaseline="middle">
            {s.name}
          </text>
        ))}
        {hoverH != null ? <line x1={x(hoverH)} x2={x(hoverH)} y1={M.t} y2={H - M.b} className={styles.crosshair} /> : null}
      </svg>
      <figcaption className={styles.legend}>
        {shown.map((s) => (
          <span key={s.letter}>
            <i className={styles.keySeries} style={{ borderColor: filterColor(s.letter) }} /> {s.name}
          </span>
        ))}
        <span>○ collected · ■ goal</span>
        <span className={styles.readout} aria-live="polite">
          {hoverH != null
            ? `${hoverH.toFixed(1)} h → ${shown.map((s) => `${s.name} ${snrAt(s, hoverH).toFixed(1)}`).join(" · ")}`
            : "Hover the chart for values"}
        </span>
      </figcaption>
    </figure>
  );
}
