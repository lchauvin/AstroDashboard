"use client";

import { useEffect, useState } from "react";

import { Marks, StatTile, StatusIcon } from "../ui";
import type { Comparison as Cmp, DarkSet, FrameRow, LeakTest } from "@/lib/leaks";
import { SIDES, VERDICT, adu, setLabel } from "@/lib/leaks";
import { LineChart, type LineSeries } from "./LineChart";
import styles from "./leaks.module.css";

const BASE = "var(--series-base)";
const COMP = "var(--series-comp)";

/** Light or dark, for the engine-rendered images (red night mode uses the dark ones). */
export function useTheme(): "light" | "dark" {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const el = document.documentElement;
    const read = () => setTheme(el.dataset.theme === "dark" || el.dataset.theme === "night" ? "dark" : "light");
    read();
    const mo = new MutationObserver(read);
    mo.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return theme;
}

type Stretch = "structure" | "fit" | "absolute";
const STRETCH: { id: Stretch; label: string; caption: string }[] = [
  { id: "structure", label: "Hard ±5σ", caption: "Background taken out; ±5× the noise of the smoothed difference, so anything beyond noise shows." },
  { id: "fit", label: "Fit", caption: "Background taken out; scaled to the strongest change, for the shape of a big leak." },
  { id: "absolute", label: "With level", caption: "Includes the overall level change." },
];

const z = (v: number | null) => (v == null ? "" : ` · ${v >= 100 ? Math.round(v) : v}σ`);

/** Tint for a value on the diverging scale (text stays in text colours). */
function tint(v: number, limit: number): string {
  const t = Math.max(-1, Math.min(1, v / (limit || 1)));
  const pole = t < 0 ? "var(--div-neg)" : "var(--div-pos)";
  return `color-mix(in srgb, ${pole} ${Math.round(Math.abs(t) * 70)}%, var(--div-mid))`;
}

function frameTime(r: FrameRow): string {
  return r.time ? new Date(r.time.endsWith("Z") || r.time.includes("+") ? r.time : `${r.time}Z`).toLocaleTimeString() : "";
}

export function Comparison({ test, base, comp }: { test: LeakTest; base: DarkSet; comp: DarkSet }) {
  const [data, setData] = useState<Cmp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Stretch>("structure");
  const theme = useTheme();

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    fetch(`/engine/api/leaks/${test.id}/compare?base=${base.id}&comp=${comp.id}`, { cache: "no-store" })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `engine returned ${r.status}`);
        return body as Cmp;
      })
      .then((b) => live && setData(b))
      .catch((e) => live && setError(e instanceof Error ? e.message : "failed"));
    return () => {
      live = false;
    };
  }, [test.id, base.id, base.analysedAt, comp.id, comp.analysedAt]);

  if (error) return <p className={styles.error}>Could not compare: {error}</p>;
  if (!data || !base.result || !comp.result) return <p className="muted">Comparing…</p>;

  const b = base.result;
  const c = comp.result;
  const bl = setLabel(test, base.id);
  const cl = setLabel(test, comp.id);
  const v = VERDICT[data.verdict];
  const ver = `${base.analysedAt ?? 0}-${comp.analysedAt ?? 0}`;
  const centered = mode !== "absolute";
  const [lo, hi] = centered ? [data.display.loCentered, data.display.hiCentered] : [data.display.lo, data.display.hi];
  const masterUrl = (s: DarkSet) =>
    `/engine/api/leaks/${test.id}/sets/${s.id}/master.png?lo=${lo}&hi=${hi}&center=${centered}&v=${s.analysedAt ?? 0}`;
  const diffUrl = `/engine/api/leaks/${test.id}/diff.png?base=${base.id}&comp=${comp.id}&mode=${mode}&theme=${theme}&v=${ver}`;
  const limit = { structure: data.display.structureLimit, fit: data.display.fitLimit, absolute: data.display.absoluteLimit }[mode];
  // Zones follow the stretch: shape only (the difference's own level out), or with the level.
  const zones = data.diff.zones.flat().map((v) => (centered ? v - data.diff.background : v));
  const within = " · within noise";

  const pair = (key: "rows" | "cols"): LineSeries[] => [
    { key: "b", label: bl, color: BASE, dashed: true, x: b.structure[key].map((_, i, a) => i / (a.length - 1)), y: b.structure[key] },
    { key: "c", label: cl, color: COMP, x: c.structure[key].map((_, i, a) => i / (a.length - 1)), y: c.structure[key] },
  ];
  const frames = (key: "level" | "noise" | "std"): LineSeries[] => [
    { key: "b", label: bl, color: BASE, dashed: true, x: b.perFrame.map((_, i) => i + 1), y: b.perFrame.map((r) => r[key]) },
    { key: "c", label: cl, color: COMP, x: c.perFrame.map((_, i) => i + 1), y: c.perFrame.map((r) => r[key]) },
  ];
  const frameExtra = (key: string, i: number) => {
    const r = (key === "b" ? b : c).perFrame[i];
    return r ? `${r.file}${r.ccdTemp != null ? ` · ${r.ccdTemp} °C` : ""} ${frameTime(r)}` : null;
  };
  const maxFrames = Math.max(b.perFrame.length, c.perFrame.length);
  const pct = (x: number) => `${Math.round(x * 100)}%`;

  return (
    <div>
      <div className={`framed ${styles.verdict}`}>
        <Marks />
        <StatusIcon level={v.level} size={22} />
        <div>
          <div className={styles.verdictLabel}>{v.label}</div>
          <p style={{ marginTop: 6 }}>{data.headline}</p>
          {data.findings.length || data.notes.length ? (
            <ul className={styles.findings}>
              {[...data.notes, ...data.findings].map((f) => (
                <li key={f.text}>
                  <StatusIcon level={f.level} size={13} /> <span>{f.text}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      <div className={styles.tiles}>
        <StatTile
          label="Level change"
          value={<span className="num">{adu(data.level.delta, 2, true)} ADU</span>}
          sub={
            <>
              {data.level.base.toFixed(2)} → {data.level.comp.toFixed(2)}
              {z(data.level.z)}
              {data.level.significant ? "" : within}
            </>
          }
        />
        <StatTile
          label="Noise change"
          value={<span className="num">{adu(data.noise.delta, Math.abs(data.noise.delta) >= 0.1 ? 2 : 3, true)} ADU</span>}
          sub={
            <>
              {data.noise.base.toFixed(3)} → {data.noise.comp.toFixed(3)}
              {z(data.noise.z)}
              {data.noise.significant ? "" : within}
            </>
          }
        />
        <StatTile
          label="Gradient in the difference"
          value={<span className="num">{adu(data.diff.gradient.amp)} ADU</span>}
          sub={
            <>
              rises toward the {data.diff.gradient.side}
              {z(data.diff.gradient.z)}
              {data.diff.gradient.significant ? "" : within}
            </>
          }
        />
        <StatTile
          label="Structure beyond a gradient"
          value={<span className="num">{data.diff.residualRatio != null ? `${data.diff.residualRatio}×` : "—"}</span>}
          sub={<>the noise floor ({data.diff.residualRms.toFixed(3)} ADU rms)</>}
        />
      </div>

      <div className={styles.blockHead}>
        <div className={styles.subTitle} style={{ margin: 0 }}>
          {centered ? "Shape only: each master's background taken out" : "Masters and difference with their levels"} · one stretch for both masters
        </div>
        <div className={styles.toggle} role="group" aria-label="Difference stretch">
          {STRETCH.map((m) => (
            <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.images}>
        <figure className={styles.figure}>
          <a href={masterUrl(base)} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={masterUrl(base)} alt={`Master of ${bl}`} />
          </a>
          <figcaption>
            <span className={styles.swatch} style={{ background: BASE }} aria-hidden="true" />
            Baseline · {bl}
          </figcaption>
        </figure>
        <figure className={styles.figure}>
          <a href={masterUrl(comp)} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={masterUrl(comp)} alt={`Master of ${cl}`} />
          </a>
          <figcaption>
            <span className={styles.swatch} style={{ background: COMP }} aria-hidden="true" />
            Comparison · {cl}
          </figcaption>
        </figure>
        <figure className={styles.figure}>
          <a href={diffUrl} target="_blank" rel="noreferrer">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={diffUrl} alt="Comparison minus baseline, diverging colour scale" />
          </a>
          <div className={styles.scale}>
            <span>−{limit.toFixed(limit < 0.1 ? 3 : 2)}</span>
            <span className={styles.scaleBar} aria-hidden="true" />
            <span>+{limit.toFixed(limit < 0.1 ? 3 : 2)} ADU</span>
          </div>
          <figcaption>
            {centered ? (
              <>
                Comparison − baseline against the rest of the frame: <strong>blue got darker</strong> (light removed there),
                red brighter. A covered leak shows as a blue patch, a new one as red.{" "}
              </>
            ) : (
              <>
                Comparison − baseline: <strong>red is brighter now</strong>, blue is darker now.{" "}
              </>
            )}
            {STRETCH.find((m) => m.id === mode)?.caption}
          </figcaption>
        </figure>
      </div>
      <p className="muted" style={{ fontSize: 12.5 }}>
        Images are 4×4-binned and smoothed (difference: ~16 px) so large-scale light shows over the noise; oriented as NINA and
        PixInsight show the frames.
      </p>

      <div className={styles.twoCol}>
        <div>
          <div className={styles.subTitle}>Difference by zone (ADU{centered ? ", background taken out" : ""})</div>
          <div className={styles.zones}>
            {zones.map((val, i) => (
              <div key={i} className={styles.zone} style={{ background: tint(val, limit) }}>
                {adu(val, 2, true)}
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className={styles.subTitle}>Edges beyond the gradient</div>
          <table className={`table ${styles.edgeTable}`}>
            <thead>
              <tr>
                <th>Edge</th>
                <th className="num">Baseline</th>
                <th className="num">Comparison</th>
                <th className="num">Change</th>
                <th aria-label="Significant" />
              </tr>
            </thead>
            <tbody>
              {SIDES.map((s) => (
                <tr key={s}>
                  <td style={{ textTransform: "capitalize" }}>{s}</td>
                  <td className="num">{adu(b.structure.edges[s], 2, true)}</td>
                  <td className="num">{adu(c.structure.edges[s], 2, true)}</td>
                  <td className="num">
                    {adu(data.diff.edges[s], 2, true)}
                    <span className="muted">{z(data.diff.edgeZ[s])}</span>
                  </td>
                  <td>
                    {data.diff.edgeSignificant[s] ? (
                      <StatusIcon level={data.diff.edges[s] > 0 ? "bad" : "ok"} size={13} label={data.diff.edges[s] > 0 ? "Brighter edge" : "Darker edge"} />
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Each set&apos;s own gradient: {b.structure.gradient.amp.toFixed(2)} ADU toward the {b.structure.gradient.side} →{" "}
            {c.structure.gradient.amp.toFixed(2)} ADU toward the {c.structure.gradient.side}.
          </p>
        </div>
      </div>

      <div className={styles.twoCol}>
        <div>
          <div className={styles.subTitle}>Column profile · left → right</div>
          <LineChart series={pair("cols")} xLabel={pct} xTicks={[0, 0.5, 1]} unit=" ADU" ariaLabel="Mean of each column of the masters, left to right" />
        </div>
        <div>
          <div className={styles.subTitle}>Row profile · top → bottom</div>
          <LineChart series={pair("rows")} xLabel={pct} xTicks={[0, 0.5, 1]} unit=" ADU" ariaLabel="Mean of each row of the masters, top to bottom" />
        </div>
      </div>

      <div className={styles.twoCol}>
        <div>
          <div className={styles.subTitle}>Level of each frame</div>
          <LineChart
            series={frames("level")}
            xLabel={(x) => `frame ${x}`}
            xTicks={[1, maxFrames]}
            unit=" ADU"
            digits={3}
            extra={frameExtra}
            ariaLabel="Level of each frame in both sets"
          />
        </div>
        <div>
          <div className={styles.subTitle}>Noise of each frame</div>
          <LineChart
            series={frames("noise")}
            xLabel={(x) => `frame ${x}`}
            xTicks={[1, maxFrames]}
            unit=" ADU"
            digits={3}
            extra={frameExtra}
            ariaLabel="Pixel-to-pixel noise of each frame in both sets"
          />
        </div>
      </div>

      <details className={styles.details}>
        <summary>Frame by frame: median, mean, standard deviation</summary>
        <p className="muted" style={{ fontSize: 12.5, margin: "8px 0" }}>
          Median: of the binned frame, in 1/16 ADU steps (the raw frames are whole ADU). Level: the outlier-rejected mean the
          comparison uses. σ: all pixels, hot ones included. Noise: pixel to pixel, blind to gradients.
        </p>
        <div style={{ overflowX: "auto" }}>
          <table className={`table ${styles.frames}`}>
            <thead>
              <tr>
                <th>Set</th>
                <th>Frame</th>
                <th>Time</th>
                <th className="num">Sensor</th>
                <th className="num">Median</th>
                <th className="num">Level</th>
                <th className="num">Mean</th>
                <th className="num">σ</th>
                <th className="num">Noise</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  [bl, b, BASE],
                  [cl, c, COMP],
                ] as const
              ).flatMap(([label, s, color]) =>
                s.perFrame.map((r, i) => (
                  <tr key={`${label}-${i}`}>
                    <td>
                      <span className={styles.swatch} style={{ background: color }} aria-hidden="true" />
                      {i === 0 ? label : ""}
                    </td>
                    <td className="mono">{r.file}</td>
                    <td className="muted">{frameTime(r)}</td>
                    <td className="num">{r.ccdTemp != null ? `${r.ccdTemp} °C` : "—"}</td>
                    <td className="num">{r.median.toFixed(3)}</td>
                    <td className="num">{r.level.toFixed(3)}</td>
                    <td className="num">{r.mean.toFixed(3)}</td>
                    <td className="num">{r.std.toFixed(3)}</td>
                    <td className="num">{r.noise.toFixed(3)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
