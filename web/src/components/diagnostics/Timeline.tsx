"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { LightFrame, LightGroupResult } from "@/lib/diagnostics";
import { FLAG_LABELS, filterLetter, reasonLabel } from "@/lib/diagnostics";
import { filterColor, filterLabel, num } from "@/lib/format";
import styles from "./Timeline.module.css";

const METRICS: { key: keyof LightFrame; label: string; unit: string; digits: number }[] = [
  { key: "fwhm_median", label: "FWHM", unit: "″", digits: 2 },
  { key: "n_stars", label: "Stars", unit: "", digits: 0 },
  { key: "background_median", label: "Background", unit: " ADU", digits: 0 },
  { key: "background_gradient", label: "Gradient", unit: " σ", digits: 1 },
  { key: "transparency", label: "Transparency", unit: "", digits: 2 },
  { key: "eccentricity_median", label: "Eccentricity", unit: "", digits: 2 },
  { key: "noise_mad", label: "Noise", unit: " ADU", digits: 1 },
  { key: "score", label: "Score", unit: "", digits: 2 },
  { key: "altitude_deg", label: "Altitude", unit: "°", digits: 0 },
];

const H = 300;
const PAD = { l: 56, r: 16, t: 14, b: 30 };

function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

const timeOf = (f: LightFrame) => (f.obs_time ? Date.parse(f.obs_time.endsWith("Z") ? f.obs_time : f.obs_time + "Z") : NaN);
const clock = (t: number) => new Date(t).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });

export function Timeline({
  groups,
  onSelect,
  events = [],
  flags = {},
}: {
  groups: LightGroupResult[];
  onSelect: (frames: LightFrame[], index: number) => void;
  events?: { kind: "flip" | "focus"; time: string; label: string }[];
  flags?: Record<string, string[]>;
}) {
  const [metric, setMetric] = useState(METRICS[0]);
  const [hover, setHover] = useState<{ f: LightFrame; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const all = useMemo(() => groups.flatMap((g) => g.frames).filter((f) => Number.isFinite(timeOf(f))), [groups]);
  const sorted = useMemo(() => [...all].sort((a, b) => timeOf(a) - timeOf(b)), [all]);
  const vals = all.map((f) => f[metric.key]).filter((v): v is number => typeof v === "number");
  if (!all.length || !vals.length) return <p className="muted">No timed frames to plot.</p>;

  const t0 = timeOf(sorted[0]);
  const t1 = Math.max(timeOf(sorted[sorted.length - 1]), t0 + 60_000);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const padY = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
  const yTicks = niceTicks(lo - padY, hi + padY);
  const y0 = Math.min(lo - padY, yTicks[0]);
  const y1 = Math.max(hi + padY, yTicks[yTicks.length - 1]);
  const W = width;
  const x = (t: number) => PAD.l + ((t - t0) / (t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const hourTicks: number[] = [];
  for (let t = Math.ceil(t0 / 3_600_000) * 3_600_000; t <= t1; t += 3_600_000) hourTicks.push(t);

  // Twilight bands: runs of consecutive frames with the sun above −18° (astronomical) / −12° (nautical).
  const bands: { from: number; to: number; deep: boolean }[] = [];
  sorted.forEach((f, i) => {
    if (f.sunAlt == null || f.sunAlt < -18) return;
    const prev = sorted[i - 1];
    const next = sorted[i + 1];
    const from = prev ? (timeOf(prev) + timeOf(f)) / 2 : timeOf(f);
    const to = next ? (timeOf(f) + timeOf(next)) / 2 : timeOf(f);
    bands.push({ from, to, deep: f.sunAlt >= -12 });
  });

  const letters = [...new Set(groups.map((g) => filterLetter(g.filter) ?? g.filter))];

  return (
    <div>
      <div className={styles.head}>
        <h2 className="section-title">Through the night</h2>
        <label className={styles.metric}>
          <span className="eyebrow">Metric</span>
          <select value={metric.key} onChange={(e) => setMetric(METRICS.find((m) => m.key === e.target.value) ?? METRICS[0])}>
            {METRICS.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.legend}>
        {letters.map((l) => (
          <span key={l}>
            <svg width="10" height="10" aria-hidden="true">
              <circle cx="5" cy="5" r="4" fill={filterColor(l)} />
            </svg>
            {filterLabel(l)}
          </span>
        ))}
        <span>
          <svg width="10" height="10" aria-hidden="true">
            <circle cx="5" cy="5" r="3.5" fill="none" stroke="var(--text-secondary)" strokeWidth="1.5" />
          </svg>
          rejected
        </span>
        {bands.length ? (
          <span>
            <span className={styles.twilightSwatch} aria-hidden="true" /> twilight (sun above −18°)
          </span>
        ) : null}
        {events.some((e) => e.kind === "focus") ? (
          <span>
            <svg width="10" height="10" aria-hidden="true">
              <path d="M1 1 L9 1 L5 8 Z" fill="var(--text-muted)" />
            </svg>
            autofocus
          </span>
        ) : null}
        {events.some((e) => e.kind === "flip") ? (
          <span>
            <svg width="12" height="10" aria-hidden="true">
              <line x1="6" y1="0" x2="6" y2="10" stroke="var(--color-accent)" strokeDasharray="2 2" strokeWidth="1.5" />
            </svg>
            meridian flip
          </span>
        ) : null}
      </div>
      <div ref={box} className={styles.chart} onMouseLeave={() => setHover(null)}>
        <svg width={W} height={H} role="img" aria-label={`${metric.label} of every frame through the night`}>
          {bands.map((b, i) => (
            <rect
              key={i}
              x={x(b.from)}
              y={PAD.t}
              width={Math.max(2, x(b.to) - x(b.from))}
              height={H - PAD.t - PAD.b}
              className={b.deep ? styles.twilightDeep : styles.twilight}
            />
          ))}
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className={styles.grid} />
              <text x={PAD.l - 8} y={y(v)} className={styles.tick} textAnchor="end" dominantBaseline="middle">
                {num(v, metric.digits > 0 && Math.abs(y1 - y0) < 10 ? metric.digits : 0)}
              </text>
            </g>
          ))}
          {hourTicks.map((t) => (
            <text key={t} x={x(t)} y={H - 10} className={styles.tick} textAnchor="middle">
              {clock(t)}
            </text>
          ))}
          <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} className={styles.axis} />
          {events.map((e, i) => {
            const t = Date.parse(e.time);
            if (!Number.isFinite(t) || t < t0 || t > t1) return null;
            const ex = x(t);
            return e.kind === "flip" ? (
              <line key={i} x1={ex} x2={ex} y1={PAD.t} y2={H - PAD.b} className={styles.flip}>
                <title>{e.label}</title>
              </line>
            ) : (
              <path key={i} d={`M${ex - 4} ${PAD.t - 10} L${ex + 4} ${PAD.t - 10} L${ex} ${PAD.t - 3} Z`} className={styles.focus}>
                <title>{e.label}</title>
              </path>
            );
          })}
          {groups.map((g) =>
            g.frames.map((f, i) => {
              const v = f[metric.key];
              const t = timeOf(f);
              if (typeof v !== "number" || !Number.isFinite(t)) return null;
              const color = filterColor(filterLetter(g.filter));
              const cx = x(t);
              const cy = y(v);
              return (
                <g key={f.path}>
                  <circle
                    cx={cx}
                    cy={cy}
                    r={f.rejected ? 4.5 : 4}
                    fill={f.rejected ? "var(--color-bg)" : color}
                    stroke={f.rejected ? color : "var(--color-bg)"}
                    strokeWidth={f.rejected ? 1.8 : 1.5}
                  />
                  <circle
                    cx={cx}
                    cy={cy}
                    r={10}
                    fill="transparent"
                    style={{ cursor: "pointer" }}
                    onMouseEnter={() => setHover({ f, x: cx, y: cy })}
                    onClick={() => onSelect(g.frames, i)}
                  />
                </g>
              );
            }),
          )}
        </svg>
        {hover ? (
          <div
            className={styles.tooltip}
            style={{ left: Math.min(hover.x + 12, W - 250), top: Math.max(0, hover.y - 70) }}
            role="status"
          >
            <strong>
              {filterLabel(filterLetter(hover.f.filter_name))} · {clock(timeOf(hover.f))}
            </strong>
            <br />
            <span className="num">
              {metric.label} {num(hover.f[metric.key] as number | null, metric.digits)}
              {metric.unit}
            </span>
            <br />
            {hover.f.rejected ? `Rejected: ${hover.f.reasons.map(reasonLabel).join(", ")}` : "Kept"}
            {flags[hover.f.path]?.length ? (
              <>
                <br />
                Findings: {flags[hover.f.path].map((c) => FLAG_LABELS[c] ?? c).join(", ")}
              </>
            ) : null}
            <br />
            <span className="muted">click to preview</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
