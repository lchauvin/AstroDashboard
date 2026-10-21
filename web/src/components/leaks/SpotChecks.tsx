"use client";

import { useEffect, useMemo, useState } from "react";
import { Pencil, RotateCw, Trash2 } from "lucide-react";

import { Marks, StatusIcon } from "../ui";
import type { LeakTest, SpotCheck, SpotFrame } from "@/lib/leaks";
import { SPOT, adu, settingText } from "@/lib/leaks";
import { useTheme } from "./Comparison";
import styles from "./leaks.module.css";

async function send(url: string, method: string, body?: unknown): Promise<LeakTest> {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const out = await res.json();
  if (!res.ok) throw new Error(typeof out.detail === "string" ? out.detail : `engine returned ${res.status}`);
  return out as LeakTest;
}

function clock(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
}

/** What the flashlight was on; saved when the field loses focus. */
function SpotInput({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <input
      className={styles.spotInput}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text.trim() !== value && onSave(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      placeholder="Flashlight on…"
      maxLength={500}
      aria-label="What the flashlight was on"
    />
  );
}

function Verdict({ f }: { f: SpotFrame }) {
  const v = SPOT[f.verdict];
  return (
    <span className={styles.verdictCell}>
      <StatusIcon level={v.level} size={13} /> {v.label}
      {f.where ? <span className={styles.where}>most light in the {f.where.zone}</span> : null}
    </span>
  );
}

function CheckCard({ test, check, onChange }: { test: LeakTest; check: SpotCheck; onChange: (t: LeakTest) => void }) {
  const theme = useTheme();
  const [scale, setScale] = useState<"shared" | "own">("shared");
  const [order, setOrder] = useState<"time" | "light">("time");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(check.comment);
  const [error, setError] = useState<string | null>(null);
  const ev = check.evaluation;
  const base = `/engine/api/leaks/${test.id}/checks/${check.id}`;

  const act = async (fn: () => Promise<LeakTest>) => {
    setError(null);
    try {
      onChange(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  };
  const patchFrame = (index: number, change: { role?: "reference" | "probe"; comment?: string }) =>
    act(() => send(base, "PATCH", { frames: [{ index, ...change }] }));

  const rows = useMemo(() => {
    if (!ev) return [];
    const list = [...ev.frames];
    if (order === "light") list.sort((a, b) => (a.verdict === "reference" ? 1 : 0) - (b.verdict === "reference" ? 1 : 0) || b.levelDelta - a.levelDelta);
    return list;
  }, [ev, order]);
  const maxLight = ev ? Math.max(0.01, ...ev.frames.filter((f) => f.verdict !== "reference").map((f) => f.levelDelta)) : 1;
  const leaks = ev ? ev.frames.filter((f) => f.verdict === "leak") : [];

  return (
    <div className={`framed ${styles.check}`}>
      <Marks />
      <div className={styles.blockHead} style={{ marginBottom: 6 }}>
        {renaming ? (
          <form
            className={styles.row}
            onSubmit={(e) => {
              e.preventDefault();
              setRenaming(false);
              void act(() => send(base, "PATCH", { comment: title }));
            }}
          >
            <input className={styles.input} value={title} onChange={(e) => setTitle(e.target.value)} autoFocus maxLength={2000} aria-label="Spot check title" />
            <button type="submit" className="button button-primary">
              Save
            </button>
          </form>
        ) : (
          <div className={`${styles.comment} ${check.comment ? "" : styles.commentEmpty}`}>{check.comment || "Spot check"}</div>
        )}
        <div className={styles.row} style={{ gap: 14 }}>
          <button type="button" className={styles.link} onClick={() => setRenaming(true)}>
            <Pencil size={12} strokeWidth={1.8} aria-hidden="true" /> Rename
          </button>
          {check.status === "done" || check.status === "error" ? (
            <button type="button" className={styles.link} onClick={() => act(() => send(`${base}/rerun`, "POST"))}>
              <RotateCw size={12} strokeWidth={1.8} aria-hidden="true" /> Re-analyse
            </button>
          ) : null}
          <button type="button" className={styles.link} onClick={() => act(() => send(base, "DELETE"))} title="Removes the check from this test; the frames stay where they are">
            <Trash2 size={12} strokeWidth={1.8} aria-hidden="true" /> Remove
          </button>
        </div>
      </div>
      <div className={styles.source}>
        {check.folder} · {settingText(check.want)}
        {check.referenceFolder ? ` · no-flashlight frames from ${check.referenceFolder}` : ""}
      </div>

      {check.status === "queued" || check.status === "running" ? (
        <>
          <div className={styles.progress} aria-hidden="true">
            <span style={{ width: check.progress ? `${(100 * check.progress.done) / Math.max(1, check.progress.total)}%` : "0%" }} />
          </div>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
            {check.progress ? `Reading frame ${Math.min(check.progress.done + 1, check.progress.total)} of ${check.progress.total}` : "Waiting…"}
          </div>
        </>
      ) : null}
      {check.status === "error" ? <p className={styles.error} style={{ marginTop: 6 }}>Analysis failed: {check.error}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}

      {ev ? (
        <>
          <div className={styles.checkSummary}>
            <p>
              <StatusIcon level={leaks.length ? "bad" : "ok"} size={14} />{" "}
              <strong>
                {leaks.length
                  ? `Light gets in on ${leaks.length} of ${ev.frames.filter((f) => f.verdict !== "reference").length} frames`
                  : "No light got in on any frame"}
              </strong>
              {leaks.length ? (
                <span className="muted">
                  {" "}
                  —{" "}
                  {[...leaks]
                    .sort((a, b) => b.levelDelta - a.levelDelta)
                    .map((f) => `${check.frames[f.index].comment || `frame ${f.index + 1}`} (${adu(f.levelDelta, 2, true)})`)
                    .join(", ")}
                </span>
              ) : null}
            </p>
            {ev.counts.step ? (
              <p>
                <StatusIcon level="info" size={14} /> {ev.counts.step} frame{ev.counts.step > 1 ? "s" : ""} changed level without
                extra noise: the camera&apos;s bias stepping, not light.
              </p>
            ) : null}
            {check.autoReference || ev.reference.auto ? (
              <p>
                <StatusIcon level="warn" size={14} /> Nothing was marked as taken without the flashlight, so the{" "}
                {ev.reference.frames.length} frames with the lowest noise are the reference. Toggle <em>Reference</em> on the frames
                you shot with the flashlight off.
              </p>
            ) : null}
            <p className="muted" style={{ fontSize: 12.5 }}>
              Reference: {ev.reference.frames.length} frame{ev.reference.frames.length > 1 ? "s" : ""}, level {ev.reference.level.toFixed(2)}, noise{" "}
              {ev.reference.noise.toFixed(3)} ADU. Light raises the pixel noise (its own shot noise): a frame counts as a leak when
              its noise rises by {ev.thresholds.noise.toFixed(3)} ADU or more, or its pattern reaches {ev.thresholds.structureRatio}× the noise. A level
              change alone (≥ {ev.thresholds.level.toFixed(2)} ADU) is a bias step.
            </p>
          </div>

          <div className={styles.blockHead} style={{ margin: "14px 0 8px" }}>
            <div className={styles.toggle} role="group" aria-label="Order">
              <button type="button" aria-pressed={order === "time"} onClick={() => setOrder("time")}>
                As shot
              </button>
              <button type="button" aria-pressed={order === "light"} onClick={() => setOrder("light")}>
                Most light first
              </button>
            </div>
            <div className={styles.row} style={{ alignItems: "center" }}>
              <span className={styles.scale} style={{ marginTop: 0, minWidth: 220 }}>
                <span>−</span>
                <span className={styles.scaleBar} aria-hidden="true" />
                <span>+ light</span>
              </span>
              <div className={styles.toggle} role="group" aria-label="Image scale">
                <button type="button" aria-pressed={scale === "shared"} onClick={() => setScale("shared")}>
                  One scale ±{ev.sharedLimit.toFixed(2)}
                </button>
                <button type="button" aria-pressed={scale === "own"} onClick={() => setScale("own")}>
                  Each its own
                </button>
              </div>
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table className={`table ${styles.spotTable}`}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Frame − reference</th>
                  <th>Flashlight on</th>
                  <th>Role</th>
                  <th className="num">Level</th>
                  <th className="num">Noise</th>
                  <th className="num">Pattern</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => {
                  const fr = check.frames[f.index];
                  const isRef = fr.role === "reference";
                  return (
                    <tr key={f.index} className={f.inReference ? styles.refRow : undefined}>
                      <td className="num">
                        {f.index + 1}
                        <div className="muted" style={{ fontSize: 11 }}>
                          {clock(f.time)}
                        </div>
                      </td>
                      <td>
                        <a href={`${base}/frames/${f.index}/diff.png?scale=${scale}&theme=${theme}&v=${check.analysedAt ?? 0}`} target="_blank" rel="noreferrer">
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            className={styles.spotThumb}
                            src={`${base}/frames/${f.index}/diff.png?scale=${scale}&theme=${theme}&v=${check.analysedAt ?? 0}-${ev.reference.frames.join(".")}`}
                            alt={`Frame ${f.index + 1} minus the reference`}
                            loading="lazy"
                          />
                        </a>
                      </td>
                      <td style={{ minWidth: 180 }}>
                        <SpotInput value={fr.comment} onSave={(v) => patchFrame(f.index, { comment: v })} />
                        <div className="mono muted" style={{ fontSize: 10.5, marginTop: 3 }}>
                          {f.file}
                        </div>
                      </td>
                      <td>
                        <button
                          type="button"
                          className={styles.roleToggle}
                          aria-pressed={isRef}
                          onClick={() => patchFrame(f.index, { role: isRef ? "probe" : "reference" })}
                          title={isRef ? "Taken without the flashlight: part of the reference" : "Mark as taken without the flashlight"}
                        >
                          {isRef ? "Reference" : "Probe"}
                        </button>
                      </td>
                      <td className="num">
                        {adu(f.levelDelta, 2, true)}
                        {f.verdict !== "reference" ? (
                          <span className={styles.lightBar} aria-hidden="true">
                            <span style={{ width: `${Math.max(0, Math.min(100, (100 * f.levelDelta) / maxLight))}%` }} />
                          </span>
                        ) : null}
                      </td>
                      <td className={`num ${f.noiseSignificant ? styles.strong : ""}`}>{adu(f.noiseDelta, 3, true)}</td>
                      <td className={`num ${f.structureSignificant ? styles.strong : ""}`}>{f.structureRatio != null ? `${f.structureRatio}×` : "—"}</td>
                      <td>
                        <Verdict f={f} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
            Level and noise are the change from the reference, in ADU; pattern is how far the frame&apos;s shape departs from the
            reference, in multiples of the noise. Images: each frame minus the reference master, smoothed, with its level — red is
            extra light.
          </p>
        </>
      ) : null}
    </div>
  );
}

export function SpotChecks({ test, onChange }: { test: LeakTest; onChange: (t: LeakTest) => void }) {
  if (!test.checks.length) return <p className="muted">No spot checks yet.</p>;
  return (
    <div className={styles.sets}>
      {test.checks.map((c) => (
        <CheckCard key={c.id} test={test} check={c} onChange={onChange} />
      ))}
    </div>
  );
}
