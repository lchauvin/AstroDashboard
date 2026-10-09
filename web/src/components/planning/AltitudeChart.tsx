"use client";

import { useState } from "react";

import type { SeriesPoint } from "@/lib/planning/series";
import styles from "./charts.module.css";

const W = 760;
const H = 260;
const M = { l: 38, r: 14, t: 14, b: 26 };

const time = (ms: number) => new Date(ms).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });

/**
 * One night: target altitude (accent), moon altitude (dashed, context), the usable
 * floor at the target's azimuth (dotted), twilight and darkness bands, and the
 * usable time filled under the target line. Single axis, degrees.
 */
export function AltitudeChart({ points, title }: { points: SeriesPoint[]; title: string }) {
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const t0 = points[0].t;
  const t1 = points[points.length - 1].t;
  const x = (t: number) => M.l + ((t - t0) / (t1 - t0)) * (W - M.l - M.r);
  const y = (alt: number) => M.t + ((90 - Math.max(0, Math.min(90, alt))) / 90) * (H - M.t - M.b);
  const r = (v: number) => Math.round(v * 10) / 10;
  const line = (get: (p: SeriesPoint) => number) => points.map((p, i) => `${i ? "L" : "M"}${r(x(p.t))},${r(y(get(p)))}`).join(" ");
  // The moon is drawn only while it is up: the line breaks where it sets.
  const moonPath = points
    .map((p, i) => (p.moonAlt > 0 ? `${i > 0 && points[i - 1].moonAlt > 0 ? "L" : "M"}${r(x(p.t))},${r(y(p.moonAlt))}` : ""))
    .join(" ");

  // Bands from the sun's altitude: twilight (0 … −18°) and darkness (< −18°).
  const bands: { from: number; to: number; kind: "twilight" | "dark" }[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const s = points[i].sunAlt;
    const kind = s < -18 ? "dark" : s < 0 ? "twilight" : null;
    if (!kind) continue;
    const last = bands[bands.length - 1];
    if (last && last.kind === kind && last.to === points[i].t) last.to = points[i + 1].t;
    else bands.push({ from: points[i].t, to: points[i + 1].t, kind });
  }
  // Usable segments: dark and above the floor.
  const usable: SeriesPoint[][] = [];
  let run: SeriesPoint[] = [];
  for (const p of points) {
    if (p.sunAlt < -18 && p.alt >= p.floor) run.push(p);
    else if (run.length) {
      usable.push(run);
      run = [];
    }
  }
  if (run.length) usable.push(run);

  const ticks: number[] = [];
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  for (let t = first.getTime() + 3_600_000; t < t1; t += 3_600_000) ticks.push(t);
  const hp = hover != null ? points[hover] : null;

  return (
    <figure className={styles.figure}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.svg}
        role="img"
        aria-label={`${title}: altitude through the night`}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const t = t0 + ((px - M.l) / (W - M.l - M.r)) * (t1 - t0);
          let best = 0;
          for (let i = 1; i < points.length; i++) if (Math.abs(points[i].t - t) < Math.abs(points[best].t - t)) best = i;
          setHover(px >= M.l && px <= W - M.r ? best : null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {bands.map((b) => (
          <rect key={b.from} x={x(b.from)} y={M.t} width={Math.max(0, x(b.to) - x(b.from))} height={H - M.t - M.b} className={b.kind === "dark" ? styles.bandDark : styles.bandTwilight} />
        ))}
        {[0, 30, 60, 90].map((a) => (
          <g key={a}>
            <line x1={M.l} x2={W - M.r} y1={y(a)} y2={y(a)} className={styles.grid} />
            <text x={M.l - 6} y={y(a)} className={styles.axis} textAnchor="end" dominantBaseline="middle">
              {a}°
            </text>
          </g>
        ))}
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 8} className={styles.axis} textAnchor="middle">
            {time(t).slice(0, 2)}h
          </text>
        ))}
        {usable.map((seg) => (
          <path
            key={seg[0].t}
            d={`M${r(x(seg[0].t))},${r(y(seg[0].floor))} ${seg.map((p) => `L${r(x(p.t))},${r(y(p.alt))}`).join(" ")} L${r(x(seg[seg.length - 1].t))},${r(y(seg[seg.length - 1].floor))} Z`}
            className={styles.usable}
          />
        ))}
        <path d={line((p) => p.floor)} className={styles.floor} />
        {moonPath.trim() ? <path d={moonPath} className={styles.moonLine} /> : null}
        <path d={line((p) => p.alt)} className={styles.targetLine} />
        {hp ? (
          <g>
            <line x1={x(hp.t)} x2={x(hp.t)} y1={M.t} y2={H - M.b} className={styles.crosshair} />
            <circle cx={x(hp.t)} cy={y(hp.alt)} r={4} className={styles.targetDot} />
            {hp.moonAlt > 0 ? <circle cx={x(hp.t)} cy={y(hp.moonAlt)} r={3.5} className={styles.moonDot} /> : null}
          </g>
        ) : null}
      </svg>
      <figcaption className={styles.legend}>
        <span>
          <i className={styles.keyTarget} /> {title}
        </span>
        <span>
          <i className={styles.keyMoon} /> Moon
        </span>
        <span>
          <i className={styles.keyFloor} /> Usable floor at the target&apos;s azimuth
        </span>
        <span>
          <i className={styles.keyUsable} /> Usable (dark, above the floor)
        </span>
        <span className={styles.readout} aria-live="polite">
          {hp
            ? `${time(hp.t)} · ${title} ${hp.alt.toFixed(0)}° az ${hp.az.toFixed(0)}° · moon ${hp.moonAlt > 0 ? `${hp.moonAlt.toFixed(0)}°` : "down"} · sun ${hp.sunAlt.toFixed(0)}°`
            : "Hover the chart for values"}
        </span>
      </figcaption>
    </figure>
  );
}
