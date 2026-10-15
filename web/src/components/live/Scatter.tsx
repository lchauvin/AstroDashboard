"use client";

import { useState } from "react";

import styles from "./charts.module.css";

/** RA (x) against Dec (y) error, like PHD2's target view. Rings at the target RMS and twice it. */
export function Scatter({ ra, dec, excluded, ring, size = 240 }: { ra: number[]; dec: number[]; excluded?: boolean[]; ring: number; size?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = ra.map((r, i) => ({ r, d: dec[i], i })).filter((p) => !excluded?.[p.i]);
  // Scale to the 95th percentile so one excursion does not shrink the cloud; outliers sit on the edge.
  const mags = pts.map((p) => Math.max(Math.abs(p.r), Math.abs(p.d))).sort((a, b) => a - b);
  const peak = Math.max(ring * 2.2, mags[Math.floor(mags.length * 0.95)] ?? 0);
  const lim = [1, 1.5, 2, 3, 4, 6, 8, 12, 20].find((s) => s >= peak) ?? peak;
  const c = size / 2;
  const k = (size / 2 - 14) / lim;
  const clampV = (v: number) => Math.max(-lim, Math.min(lim, v));
  const newest = pts.length - 1;

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * size;
    const my = ((e.clientY - rect.top) / rect.height) * size;
    let best: number | null = null;
    let bestD = 14 * 14;
    pts.forEach((p, j) => {
      const dx = c + clampV(p.r) * k - mx;
      const dy = c - clampV(p.d) * k - my;
      if (dx * dx + dy * dy < bestD) {
        bestD = dx * dx + dy * dy;
        best = j;
      }
    });
    setHover(best);
  };

  const hp = hover !== null ? pts[hover] : null;
  return (
    <div className={styles.chart} style={{ width: "100%", maxWidth: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} width="100%" role="img" aria-label="Guide error scatter, RA against Dec" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <line x1={c} x2={c} y1={8} y2={size - 8} className={styles.grid} />
        <line x1={8} x2={size - 8} y1={c} y2={c} className={styles.grid} />
        {[ring, ring * 2].map((r, i) => (
          <circle key={i} cx={c} cy={c} r={r * k} className={i ? styles.grid : styles.ring} />
        ))}
        <text x={c + ring * k * 0.71 + 3} y={c - ring * k * 0.71 - 3} className={styles.axis}>
          {ring.toFixed(2)}″
        </text>
        <text x={size - 10} y={c - 4} textAnchor="end" className={styles.axis}>
          RA
        </text>
        <text x={c + 4} y={14} className={styles.axis}>
          Dec
        </text>
        <text x={size - 10} y={size - 6} textAnchor="end" className={styles.axis}>
          ±{lim}″
        </text>
        {pts.map((p, j) => (
          <circle key={p.i} cx={c + clampV(p.r) * k} cy={c - clampV(p.d) * k} r={j === newest ? 4 : 2.2} className={j === newest ? styles.scatterNew : styles.scatterDot}
            style={{ opacity: j === newest ? 1 : 0.25 + 0.6 * (j / Math.max(1, newest)) }} />
        ))}
        {hp ? <circle cx={c + clampV(hp.r) * k} cy={c - clampV(hp.d) * k} r={5} className={styles.dotRing} /> : null}
      </svg>
      {hp ? (
        <div className={styles.tooltip} style={{ left: 8, top: 8 }}>
          <div className={styles.ttRow}>RA <b className="num">{hp.r.toFixed(2)}″</b></div>
          <div className={styles.ttRow}>Dec <b className="num">{hp.d.toFixed(2)}″</b></div>
        </div>
      ) : null}
    </div>
  );
}
