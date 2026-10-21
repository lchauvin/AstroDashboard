"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import styles from "./leaks.module.css";

export type LineSeries = { key: string; label: string; color: string; dashed?: boolean; x: number[]; y: number[] };

type Props = {
  series: LineSeries[];
  xLabel: (x: number) => string;
  xTicks?: number[];
  unit: string;
  digits?: number;
  height?: number;
  ariaLabel: string;
  /** Extra tooltip line for a series at a point index. */
  extra?: (key: string, i: number) => string | null;
};

const PAD = { l: 56, r: 14, t: 12, b: 24 };

function niceStep(span: number, target: number): number {
  const raw = span / Math.max(1, target);
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? raw;
}

/** One y-axis shared by every series; crosshair + tooltip at the nearest x. */
export function LineChart({ series, xLabel, xTicks, unit, digits = 2, height = 200, ariaLabel, extra }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(560);
  const [px, setPx] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { x0, x1, y0, y1, ticks } = useMemo(() => {
    const xs = series.flatMap((s) => s.x);
    const ys = series.flatMap((s) => s.y.filter(Number.isFinite));
    let lo = Math.min(...ys);
    let hi = Math.max(...ys);
    if (!Number.isFinite(lo)) [lo, hi] = [0, 1];
    if (hi - lo < 1e-3) [lo, hi] = [lo - 0.05, hi + 0.05];
    const step = niceStep(hi - lo, 4);
    const a = Math.floor(lo / step) * step;
    const b = Math.ceil(hi / step) * step;
    const t: number[] = [];
    for (let v = a; v <= b + step / 2; v += step) t.push(v);
    return { x0: Math.min(...xs), x1: Math.max(...xs), y0: a, y1: b, ticks: t };
  }, [series]);

  const h = height;
  const iw = w - PAD.l - PAD.r;
  const ih = h - PAD.t - PAD.b;
  const sx = (v: number) => PAD.l + (x1 > x0 ? ((v - x0) / (x1 - x0)) * iw : iw / 2);
  const sy = (v: number) => PAD.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
  const stepDigits = Math.max(0, -Math.floor(Math.log10((y1 - y0) / 4 || 1)) + 1);

  const hover = px == null ? null : x0 + ((px - PAD.l) / iw) * (x1 - x0);
  const nearest = (s: LineSeries) => {
    if (hover == null || !s.x.length) return -1;
    let best = 0;
    for (let i = 1; i < s.x.length; i++) if (Math.abs(s.x[i] - hover) < Math.abs(s.x[best] - hover)) best = i;
    return best;
  };
  const hits = series.map((s) => ({ s, i: nearest(s) })).filter((h) => h.i >= 0 && Number.isFinite(h.s.y[h.i]));
  const hx = hits.length ? sx(hits[0].s.x[hits[0].i]) : null;

  const path = (s: LineSeries) => {
    let d = "";
    let pen = false;
    s.x.forEach((xv, i) => {
      const yv = s.y[i];
      if (!Number.isFinite(yv)) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${sx(xv).toFixed(1)},${sy(yv).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  return (
    <div className={styles.chart} ref={box}>
      {series.length > 1 ? (
        <div className={styles.legend}>
          {series.map((s) => (
            <span key={s.key}>
              <svg width="18" height="8" aria-hidden="true">
                <line x1="1" y1="4" x2="17" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? "4 3" : undefined} />
              </svg>
              {s.label}
            </span>
          ))}
        </div>
      ) : null}
      <svg
        width="100%"
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={ariaLabel}
        onPointerMove={(e) => {
          // Through the viewBox: the page's CSS zoom makes client px and layout px differ.
          const r = e.currentTarget.getBoundingClientRect();
          setPx(((e.clientX - r.left) / r.width) * w);
        }}
        onPointerLeave={() => setPx(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className={styles.grid} x1={PAD.l} x2={w - PAD.r} y1={sy(t)} y2={sy(t)} />
            <text className={styles.axis} x={PAD.l - 6} y={sy(t) + 3} textAnchor="end">
              {t.toFixed(stepDigits)}
            </text>
          </g>
        ))}
        {(xTicks ?? [x0, x1]).map((t) => (
          <text key={t} className={styles.axis} x={sx(t)} y={h - 6} textAnchor={t === x0 ? "start" : t === x1 ? "end" : "middle"}>
            {xLabel(t)}
          </text>
        ))}
        {series.map((s) => (
          <path key={s.key} d={path(s)} className={styles.line} stroke={s.color} strokeDasharray={s.dashed ? "5 4" : undefined} />
        ))}
        {hx != null ? <line className={styles.crosshair} x1={hx} x2={hx} y1={PAD.t} y2={PAD.t + ih} /> : null}
        {hits.map(({ s, i }) => (
          <circle key={s.key} cx={sx(s.x[i])} cy={sy(s.y[i])} r={4} fill={s.color} className={styles.dot} />
        ))}
      </svg>
      {hits.length && hx != null ? (
        <div className={styles.tooltip} style={{ left: `${(100 * Math.min(hx + 12, w - 200)) / w}%`, top: PAD.t }}>
          <div className="eyebrow">{xLabel(hits[0].s.x[hits[0].i])}</div>
          {hits.map(({ s, i }) => (
            <div key={s.key}>
              <span className={styles.swatch} style={{ background: s.color }} aria-hidden="true" />
              {series.length > 1 ? `${s.label}: ` : ""}
              <span className="num">
                {s.y[i].toFixed(digits)}
                {unit}
              </span>
              {extra?.(s.key, i) ? <div className="muted">{extra(s.key, i)}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
