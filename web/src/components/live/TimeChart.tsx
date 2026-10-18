"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import styles from "./charts.module.css";

/** Non-finite values (NaN) are gaps: the line breaks there and the tooltip leaves the series out. */
export type Series = { key: string; label: string; color: string; values: number[] };

type Props = {
  t: number[]; // seconds relative to now (≤ 0)
  series: Series[];
  seconds: number; // x domain: [-seconds, 0]
  unit: string; // "″" or " ms"
  digits?: number;
  limit?: number | "auto"; // symmetric y limit
  minLimit?: number;
  excluded?: boolean[];
  events?: { t: number; label: string }[];
  height?: number;
  extra?: (i: number) => string | null; // extra tooltip line
  ariaLabel: string;
};

const PAD = { l: 44, r: 34, t: 18, b: 22 };

function xLabel(s: number): string {
  if (s === 0) return "now";
  const m = Math.round(s / 60);
  if (Math.abs(s) >= 7200) return `−${(Math.abs(s) / 3600).toFixed(Math.abs(s) % 3600 ? 1 : 0)} h`;
  return Math.abs(s) >= 60 ? `−${Math.abs(m)} min` : `−${Math.abs(Math.round(s))} s`;
}

function agoLabel(s: number): string {
  const a = Math.abs(s);
  if (a >= 3600) return `${Math.floor(a / 3600)} h ${String(Math.floor((a % 3600) / 60)).padStart(2, "0")} min ago`;
  return a < 60 ? `${Math.round(a)} s ago` : `${Math.floor(a / 60)} min ${String(Math.round(a % 60)).padStart(2, "0")} s ago`;
}

/** One y-axis, symmetric about zero; series share it. Gaps in the data break the line. */
export function TimeChart({ t, series, seconds, unit, digits = 2, limit = "auto", minLimit = 1, excluded, events = [], height = 220, extra, ariaLabel }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  // Where the pointer is (seconds before now), not a sample index: the data refreshes under a still pointer,
  // and an index kept from the last move could point past the end of the new arrays.
  const [pointer, setPointer] = useState<number | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const h = height;
  const iw = w - PAD.l - PAD.r;
  const ih = h - PAD.t - PAD.b;

  const lim = useMemo(() => {
    if (limit !== "auto") return limit;
    let m = minLimit;
    for (const s of series) for (let i = 0; i < s.values.length; i++) if (!excluded?.[i] && Number.isFinite(s.values[i])) m = Math.max(m, Math.abs(s.values[i]));
    // Round up to a readable step so the axis does not jitter every frame.
    const steps = [0.5, 1, 1.5, 2, 3, 4, 5, 8, 10, 15, 20, 30, 50, 100, 200, 500, 1000, 2000, 5000];
    return steps.find((s) => s >= m * 1.05) ?? m;
  }, [limit, minLimit, series, excluded]);

  const x = (s: number) => PAD.l + ((s + seconds) / seconds) * iw;
  const y = (v: number) => PAD.t + ih / 2 - (Math.max(-lim, Math.min(lim, v)) / lim) * (ih / 2);

  // Break lines across gaps (> 3× the median frame interval).
  const gapAfter = useMemo(() => {
    const dts = t.slice(1).map((v, i) => v - t[i]).sort((a, b) => a - b);
    const med = dts.length ? dts[Math.floor(dts.length / 2)] : 2;
    return t.map((v, i) => i < t.length - 1 && t[i + 1] - v > Math.max(6, 3 * med));
  }, [t]);

  const path = (vals: number[]) => {
    let d = "";
    let pen = false;
    for (let i = 0; i < t.length; i++) {
      if (t[i] < -seconds) continue;
      if (!Number.isFinite(vals[i])) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${x(t[i]).toFixed(1)},${y(vals[i]).toFixed(1)}`;
      pen = !gapAfter[i];
    }
    return d;
  };

  const bands = useMemo(() => {
    const out: [number, number][] = [];
    if (!excluded) return out;
    let start: number | null = null;
    for (let i = 0; i < t.length; i++) {
      if (excluded[i] && start === null) start = t[i];
      if ((!excluded[i] || i === t.length - 1) && start !== null) {
        out.push([start, excluded[i] ? t[i] : t[i - 1] ?? t[i]]);
        start = null;
      }
    }
    return out;
  }, [excluded, t]);

  // Event labels: newest first wins when two would overlap.
  const shownEvents = useMemo(() => {
    const out: { t: number; label: string; text: boolean }[] = [];
    let lastX = Infinity;
    for (const e of [...events].filter((e) => e.t >= -seconds).sort((a, b) => b.t - a.t)) {
      const ex = x(e.t);
      const text = lastX - ex > 90;
      if (text) lastX = ex;
      out.push({ ...e, text });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [events, seconds, w]);

  const last = t.length - 1;
  const endLabels = useMemo(() => {
    if (last < 0) return [];
    const ys = series.filter((s) => Number.isFinite(s.values[last])).map((s) => ({ s, y: y(s.values[last]) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < ys.length; i++) if (ys[i].y - ys[i - 1].y < 12) ys[i].y = ys[i - 1].y + 12;
    return ys;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, last, lim, h]);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * w;
    setPointer(((px - PAD.l) / iw) * seconds - seconds);
  };

  // The sample nearest the pointer, found again on every render.
  const hover = useMemo(() => {
    if (pointer === null) return null;
    let best: number | null = null;
    let bestD = Infinity;
    for (let i = 0; i < t.length; i++) {
      const d = Math.abs(t[i] - pointer);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best !== null && bestD < Math.max(10, seconds / 40) ? best : null;
  }, [pointer, t, seconds]);

  const ticks = [-lim, -lim / 2, 0, lim / 2, lim];
  const xticks = seconds >= 600 ? [-seconds, -seconds * 0.75, -seconds / 2, -seconds / 4, 0] : [-seconds, -seconds / 2, 0];
  const fmtV = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}${unit}`;
  const hx = hover !== null ? x(t[hover]) : 0;

  return (
    <div className={styles.chart} ref={box}>
      <svg width={w} height={h} role="img" aria-label={ariaLabel} onPointerMove={onMove} onPointerLeave={() => setPointer(null)}>
        <defs>
          <pattern id="hatch-ex" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" className={styles.hatchLine} />
          </pattern>
        </defs>
        {bands.map(([a, b], i) => (
          <rect key={i} x={x(a)} y={PAD.t} width={Math.max(2, x(b) - x(a))} height={ih} fill="url(#hatch-ex)" className={styles.band} />
        ))}
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={w - PAD.r} y1={y(v)} y2={y(v)} className={v === 0 ? styles.zero : styles.grid} />
            <text x={PAD.l - 6} y={y(v) + 3} textAnchor="end" className={styles.axis}>
              {v === 0 ? "0" : `${v > 0 ? "+" : "−"}${Math.abs(v) >= 10 ? Math.abs(v).toFixed(0) : Math.abs(v).toFixed(1)}`}
            </text>
          </g>
        ))}
        {xticks.map((s) => (
          <text key={s} x={x(s)} y={h - 6} textAnchor={s === 0 ? "end" : s === -seconds ? "start" : "middle"} className={styles.axis}>
            {xLabel(s)}
          </text>
        ))}
        {shownEvents.map((e, i) => (
          <g key={i}>
            <line x1={x(e.t)} x2={x(e.t)} y1={PAD.t} y2={PAD.t + ih} className={styles.event} />
            {e.text ? (
              // Near the right edge the label runs leftwards from its marker instead of off the chart.
              <text x={x(e.t) > w - PAD.r - 120 ? x(e.t) - 3 : x(e.t) + 3} y={PAD.t - 5} textAnchor={x(e.t) > w - PAD.r - 120 ? "end" : "start"} className={styles.eventText}>
                {e.label}
              </text>
            ) : null}
          </g>
        ))}
        {series.map((s) => (
          <path key={s.key} d={path(s.values)} className={styles.line} style={{ stroke: s.color }} />
        ))}
        {endLabels.map(({ s, y: ly }) => (
          <text key={s.key} x={w - PAD.r + 4} y={ly + 3} className={styles.endLabel}>
            {s.label}
          </text>
        ))}
        {hover !== null ? (
          <g pointerEvents="none">
            <line x1={hx} x2={hx} y1={PAD.t} y2={PAD.t + ih} className={styles.crosshair} />
            {series.filter((s) => Number.isFinite(s.values[hover])).map((s) => (
              <circle key={s.key} cx={hx} cy={y(s.values[hover])} r={4} className={styles.dot} style={{ fill: s.color }} />
            ))}
          </g>
        ) : null}
      </svg>
      {hover !== null ? (
        <div className={styles.tooltip} style={{ left: Math.min(Math.max(hx + 12, 0), w - 190) }}>
          <div className={styles.ttHead}>{agoLabel(t[hover])}</div>
          {series.filter((s) => Number.isFinite(s.values[hover])).map((s) => (
            <div key={s.key} className={styles.ttRow}>
              <i style={{ background: s.color }} /> {s.label} <b className="num">{fmtV(s.values[hover])}</b>
            </div>
          ))}
          {extra?.(hover) ? <div className={styles.ttNote}>{extra(hover)}</div> : null}
          {excluded?.[hover] ? <div className={styles.ttNote}>Left out of the statistics (disturbance)</div> : null}
        </div>
      ) : null}
    </div>
  );
}

/** The legend row that goes above a TimeChart. Identity is never colour alone: each key has its name. */
export function Legend({ series, excluded = false, events = false }: { series: { label: string; color: string }[]; excluded?: boolean; events?: boolean }) {
  return (
    <div className={styles.legend}>
      {series.map((s) => (
        <span key={s.label}>
          <i className={styles.keyLine} style={{ borderColor: s.color }} /> {s.label}
        </span>
      ))}
      {excluded ? (
        <span>
          <i className={styles.keyHatch} /> left out (dither, settle, star lost…)
        </span>
      ) : null}
      {events ? (
        <span>
          <i className={styles.keyEvent} /> event
        </span>
      ) : null}
    </div>
  );
}
