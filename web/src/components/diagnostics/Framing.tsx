"use client";

import { useEffect, useState } from "react";

import type { LightFrame } from "@/lib/diagnostics";
import { deg, framingBusy, framingDetails, framingLevel, framingText, px, type FrameFraming, type NightCourse, type SessionFraming } from "@/lib/framing";
import { StatusIcon } from "../ui";
import type { Metric } from "./Timeline";
import styles from "./Diagnostics.module.css";
import findingStyles from "./Findings.module.css";

type State = { data: SessionFraming | null; error: string | null };

/** A night's framing against the previous night; asked again while the engine measures its lights. */
export function useSessionFraming(revId: string, folder: string, refresh?: unknown): State {
  const [state, setState] = useState<State>({ data: null, error: null });
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const res = await fetch(`/engine/api/framing/sessions/${encodeURIComponent(revId)}/${encodeURIComponent(folder)}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`engine error ${res.status}`);
        const data = (await res.json()) as SessionFraming;
        if (stop) return;
        setState({ data, error: null });
        if (framingBusy(data.job)) timer = setTimeout(load, 1500);
      } catch (e) {
        if (stop) return;
        setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
        timer = setTimeout(load, 10_000);
      }
    }
    void load();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [revId, folder, refresh]);
  return state;
}

/** Every light's framing, by Diagnostics frame path (all panels). */
export function framesOf(data: SessionFraming | null): Record<string, FrameFraming> {
  return Object.assign({}, ...(data?.panels ?? []).map((p) => p.frames));
}

/** Through the night: the dither (the shifts' spread) against the rotation's change, which dithering never makes. */
function courseText(c: NightCourse): string {
  const drift = c.rotationPx > c.ditherPx / 2;
  return (
    `Through the night: the frames shift within ${px(c.ditherPx)} (the dither); the rotation changed by ${deg(c.rotationDeg)}, ` +
    `${px(c.rotationPx)} at the corners` +
    (drift ? ": more than the dither can explain (a steady turn is field rotation; a step, the framing redone: a flip, a re-centring). See Through the night → Framing." : ".")
  );
}

type Panel = SessionFraming["panels"][number];

/** The Through the night chart's Framing metrics: each light against its night's framing, with the dither; its
 *  rotation in px at the corners, against ± the dither; and against the previous night, with "aligned up to". */
export function framingMetrics(data: SessionFraming | null, panel: string | null): Metric[] {
  const panels = (data?.panels ?? []).filter((p) => !panel || p.panel === panel);
  // != null: engines from before the night's course send no `night` at all.
  const nights = panels.filter((p): p is Panel & { night: NightCourse } => p.night != null);
  if (!nights.length) return [];
  const frames = framesOf(data);
  const at = (f: LightFrame) => frames[f.path];
  const tag = (p: Panel) => (panels.length > 1 && p.panel ? `P${p.panel} · ` : "");
  const out: Metric[] = [
    {
      key: "framing-night",
      label: "In the night (shift + rotation)",
      unit: " px",
      digits: 0,
      zero: true,
      value: (f) => at(f)?.nightPx ?? null,
      lines: nights.map((p) => ({ value: p.night.ditherPx, label: `${tag(p)}dither ${px(p.night.ditherPx)}` })),
      note: (f) => {
        const x = at(f);
        return x?.nightShiftPx == null
          ? null
          : `from the night's framing: shifted ${px(x.nightShiftPx)}, rotated ${deg(x.nightRotation)} (${px(Math.abs(x.nightRotationPx ?? 0))} at the corners)` +
              (x.outlier ? " · framed differently from the rest of the night" : "");
      },
    },
    {
      key: "framing-rotation",
      label: "Rotation (px at the corners)",
      unit: " px",
      digits: 0,
      zero: true,
      value: (f) => at(f)?.nightRotationPx ?? null,
      lines: nights.flatMap((p) => [
        { value: p.night.ditherPx, label: `${tag(p)}+ dither ${px(p.night.ditherPx)}` },
        { value: -p.night.ditherPx, label: `${tag(p)}− dither` },
      ]),
      note: (f) => {
        const r = at(f)?.nightRotation;
        return r == null ? null : `rotated ${r < 0 ? "−" : "+"}${deg(r)} from the night's framing`;
      },
    },
  ];
  const compared = panels.filter((p) => p.verdict.status === "ready" && p.verdict.frameTolerancePx != null);
  if (compared.length) {
    out.push({
      key: "framing-previous",
      label: "Vs the previous night",
      unit: " px",
      digits: 0,
      zero: true,
      value: (f) => at(f)?.cornerPx ?? null,
      lines: compared.map((p) => ({ value: p.verdict.frameTolerancePx ?? 0, label: `${tag(p)}aligned up to ${px(p.verdict.frameTolerancePx)}` })),
      note: (f) => (at(f)?.outlier ? "framed differently from the rest of this night" : null),
    });
  }
  return out;
}

/** Summary: the night against the previous one, per mosaic panel (`panel`: only that one). */
export function FramingBlock({ state, panel }: { state: State; panel: string | null }) {
  const { data, error } = state;
  const rows = (data?.panels ?? []).filter((p) => !panel || p.panel === panel);
  const job = data?.job;
  return (
    <section className={styles.block}>
      <div className={styles.blockHead}>
        <h2 className="section-title">Framing</h2>
        <span className="eyebrow">vs the previous night · shift and rotation at the corners, against the nights' spread</span>
      </div>
      {!data ? (
        <p className="muted">{error ? `Framing unavailable: ${error}` : "Loading…"}</p>
      ) : (
        <ul className={findingStyles.list}>
          {rows.map((r) => {
            const v = r.verdict;
            const counts = { ok: 0, warn: 0, bad: 0 };
            for (const f of Object.values(r.frames)) if (f.level) counts[f.level] += 1;
            const outliers = Object.values(r.frames).filter((f) => f.outlier).length;
            return (
              <li key={r.panel ?? ""} className={findingStyles.item}>
                <StatusIcon level={framingLevel(v) ?? "info"} />
                <div className={findingStyles.body}>
                  <div className={findingStyles.title}>
                    {r.panel ? `Panel ${r.panel} · ` : ""}
                    {framingText(v)}
                  </div>
                  {framingDetails(v, data.minTolerancePx).map((d) => (
                    <p key={d} className={findingStyles.text}>
                      {d}
                    </p>
                  ))}
                  {r.night ? <p className={findingStyles.text}>{courseText(r.night)}</p> : null}
                  {v.status === "ready" ? (
                    <p className={findingStyles.text}>
                      Frame by frame, against {v.previous} with this night&apos;s spread added (up to{" "}
                      {Math.round(v.frameTolerancePx ?? 0)} px): {counts.ok} aligned · {counts.warn} a little off · {counts.bad} off
                      {outliers ? ` (${outliers} framed differently from the rest of the night)` : ""}. See the Framing column under Frames.
                    </p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {job && job.status !== "failed" ? (
        <p className={styles.meta}>
          Measuring the night&apos;s stars: {job.done}/{job.total}…
        </p>
      ) : null}
      {job?.status === "failed" ? <p className={styles.meta}>Measuring failed: {job.error}</p> : null}
    </section>
  );
}
