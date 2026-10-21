"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeftRight, Pencil, RotateCw, Trash2 } from "lucide-react";

import { Marks, StatusIcon } from "../ui";
import type { DarkSet, LeakTest } from "@/lib/leaks";
import { VERDICT, adu, setLabel, settingText } from "@/lib/leaks";
import { AddSet } from "./AddSet";
import { Comparison } from "./Comparison";
import { LineChart } from "./LineChart";
import { SpotChecks } from "./SpotChecks";
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

function CommentEditor({ set, testId, onDone }: { set: DarkSet; testId: string; onDone: (t: LeakTest | null) => void }) {
  const [text, setText] = useState(set.comment);
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          onDone(await send(`/engine/api/leaks/${testId}/sets/${set.id}`, "PATCH", { comment: text }));
        } finally {
          setBusy(false);
        }
      }}
    >
      <textarea className={styles.textarea} value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} autoFocus aria-label="What changed for this set" />
      <div className={styles.row} style={{ marginTop: 6 }}>
        <button type="submit" className="button button-primary" disabled={busy}>
          Save
        </button>
        <button type="button" className={styles.link} onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function SetCard({
  test,
  set,
  index,
  onChange,
  onCompare,
}: {
  test: LeakTest;
  set: DarkSet;
  index: number;
  onChange: (t: LeakTest) => void;
  onCompare: (base: string, comp: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const r = set.result;
  const prev = set.vsPrevious;
  const act = async (fn: () => Promise<LeakTest>) => {
    setError(null);
    try {
      onChange(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    }
  };

  return (
    <div className={`framed ${styles.set}`}>
      <Marks />
      <div className={styles.setNum}>{index + 1}</div>
      {set.status === "done" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.thumb} src={`/engine/api/leaks/${test.id}/sets/${set.id}/master.png?v=${set.analysedAt ?? 0}`} alt={`Master of set ${index + 1}, own stretch`} />
      ) : (
        <div className={styles.thumbEmpty}>{set.status === "error" ? "failed" : set.status === "running" ? "analysing…" : "queued"}</div>
      )}
      <div className={styles.setInfo}>
        {editing ? (
          <CommentEditor
            set={set}
            testId={test.id}
            onDone={(t) => {
              setEditing(false);
              if (t) onChange(t);
            }}
          />
        ) : (
          <div className={`${styles.comment} ${set.comment ? "" : styles.commentEmpty}`}>
            {set.comment || (index === 0 ? "Baseline (no comment)" : "No comment")}
          </div>
        )}
        <div className={styles.source}>
          {set.folder} · {settingText(set.want)}
        </div>
        {set.status === "running" || set.status === "queued" ? (
          <>
            <div className={styles.progress} aria-hidden="true">
              <span style={{ width: set.progress ? `${(100 * set.progress.done) / Math.max(1, set.progress.total)}%` : "0%" }} />
            </div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
              {set.progress ? `Reading frame ${Math.min(set.progress.done + 1, set.progress.total)} of ${set.progress.total}` : "Waiting…"}
            </div>
          </>
        ) : null}
        {set.status === "error" ? (
          <p className={styles.error} style={{ marginTop: 6 }}>
            Analysis failed: {set.error}
          </p>
        ) : null}
        {r ? (
          <div className={styles.setStats}>
            <span>
              {r.frames}
              {r.available > r.frames ? ` of ${r.available}` : ""} frames
            </span>
            <span>
              level <span className="num">{r.level.toFixed(2)}</span>
            </span>
            <span>
              noise <span className="num">{r.noise.toFixed(3)}</span>
            </span>
            <span>
              gradient <span className="num">{r.structure.gradient.amp.toFixed(2)}</span> toward the {r.structure.gradient.side}
            </span>
            {r.ccdTemp ? <span>sensor {r.ccdTemp.median} °C</span> : null}
            {r.drift?.significant ? (
              <span>
                <StatusIcon level="warn" size={12} /> drifted {adu(r.drift.adu, 2, true)} during the set
              </span>
            ) : null}
          </div>
        ) : null}
        {prev ? (
          <div className={styles.vs}>
            <StatusIcon level={VERDICT[prev.verdict].level} size={14} />
            <span>
              <strong>{VERDICT[prev.verdict].label}</strong> vs #{index}
              {prev.levelDelta != null ? <span className="muted num"> · level {adu(prev.levelDelta, 2, true)} ADU</span> : null}
              <span className="muted"> — {prev.headline}</span>
            </span>
          </div>
        ) : null}
        {error ? <p className={styles.error}>{error}</p> : null}
      </div>
      <div className={styles.setActions}>
        {prev ? (
          <button type="button" className={styles.link} onClick={() => onCompare(prev.baseId, set.id)}>
            <ArrowLeftRight size={12} strokeWidth={1.8} aria-hidden="true" /> Compare
          </button>
        ) : null}
        <button type="button" className={styles.link} onClick={() => setEditing(true)}>
          <Pencil size={12} strokeWidth={1.8} aria-hidden="true" /> Comment
        </button>
        {set.status === "done" || set.status === "error" ? (
          <button type="button" className={styles.link} onClick={() => act(() => send(`/engine/api/leaks/${test.id}/sets/${set.id}/rerun`, "POST"))}>
            <RotateCw size={12} strokeWidth={1.8} aria-hidden="true" /> Re-analyse
          </button>
        ) : null}
        <button
          type="button"
          className={styles.link}
          onClick={() => act(() => send(`/engine/api/leaks/${test.id}/sets/${set.id}`, "DELETE"))}
          title="Removes the set from this test; the frames stay where they are"
        >
          <Trash2 size={12} strokeWidth={1.8} aria-hidden="true" /> Remove
        </button>
      </div>
    </div>
  );
}

export function LeakTestView({ initial }: { initial: LeakTest }) {
  const router = useRouter();
  const [test, setTest] = useState(initial);
  const [name, setName] = useState(initial.name);
  const [notes, setNotes] = useState(initial.notes ?? "");
  const [pair, setPair] = useState<{ base: string; comp: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const active = [...test.sets, ...test.checks].some((s) => s.status === "queued" || s.status === "running");

  // The comparison shown is part of the URL, so it can be bookmarked or reloaded.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const base = q.get("base");
    const comp = q.get("comp");
    if (base && comp) setPair({ base, comp });
  }, []);
  function choose(next: { base: string; comp: string }) {
    setPair(next);
    const q = new URLSearchParams(window.location.search);
    q.set("base", next.base);
    q.set("comp", next.comp);
    window.history.replaceState(null, "", `?${q}`);
  }

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/engine/api/leaks/${test.id}`, { cache: "no-store" });
      if (res.ok) setTest((await res.json()) as LeakTest);
    } catch {
      /* retry on the next tick */
    }
  }, [test.id]);

  useEffect(() => {
    if (!active) return;
    const t = setTimeout(refresh, 1200);
    return () => clearTimeout(t);
  }, [active, test, refresh]);

  const done = test.sets.filter((s) => s.status === "done");
  // Default comparison: the last analysed set against the analysed set before it.
  const chosen = useMemo(() => {
    const ok = (id: string) => done.some((s) => s.id === id);
    if (pair && ok(pair.base) && ok(pair.comp)) return pair;
    return done.length >= 2 ? { base: done[done.length - 2].id, comp: done[done.length - 1].id } : null;
  }, [pair, done]);
  const baseSet = chosen && test.sets.find((s) => s.id === chosen.base);
  const compSet = chosen && test.sets.find((s) => s.id === chosen.comp);

  async function saveMeta(body: { name?: string; notes?: string }) {
    try {
      setTest(await send(`/engine/api/leaks/${test.id}`, "PATCH", body));
    } catch {
      /* keep the local text; the next edit retries */
    }
  }

  const trendX = done.map((s) => test.sets.indexOf(s) + 1);

  return (
    <div className={styles.root}>
      <div className="eyebrow">Light-leak test</div>
      <input
        className={styles.nameInput}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name.trim() && name.trim() !== test.name && saveMeta({ name: name.trim() })}
        aria-label="Test name"
        maxLength={200}
      />
      <textarea
        className={`${styles.textarea} ${styles.notes}`}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onBlur={() => notes !== (test.notes ?? "") && saveMeta({ notes })}
        placeholder="Notes for the whole test: rig, light source used, where the leaks were found…"
        maxLength={5000}
      />

      <section className={styles.block}>
        <div className={styles.phase}>Phase 1 · find where light gets in</div>
        <div className={styles.blockHead}>
          <h2 className="section-title">Spot checks</h2>
          <span className="eyebrow">one frame per spot · each against frames without the flashlight</span>
        </div>
        <p className={styles.note}>
          Scope capped, room dark, one exposure per spot with a flashlight sweeping over it (focuser, joints, filter drawer…),
          plus a few frames with the flashlight off. Frames are judged one by one: light raises both the level and the pixel
          noise, so a frame where the noise rises let light in, even if the moving flashlight left no clear pattern.
        </p>
        <SpotChecks test={test} onChange={setTest} />
      </section>

      <section className={styles.block}>
        <div className={styles.blockHead}>
          <h2 className="section-title">Add a spot check</h2>
        </div>
        <AddSet test={test} kind="check" onAdded={setTest} />
      </section>

      <section className={styles.block}>
        <div className={styles.phase}>Phase 2 · fix it, set after set</div>
        <div className={styles.blockHead}>
          <h2 className="section-title">Dark sets</h2>
          <span className="eyebrow">each set is compared with the one before it</span>
        </div>
        {test.sets.length ? (
          <div className={styles.sets}>
            {test.sets.map((s, i) => (
              <SetCard key={s.id} test={test} set={s} index={i} onChange={setTest} onCompare={(base, comp) => choose({ base, comp })} />
            ))}
          </div>
        ) : (
          <p className="muted">No sets yet: add the baseline below, the rig as it is before any fix.</p>
        )}
        {done.length >= 2 ? (
          <div className={styles.trend}>
            <div>
              <div className={styles.subTitle}>Level per set</div>
              <LineChart
                series={[{ key: "l", label: "Level", color: "var(--series-base)", x: trendX, y: done.map((s) => s.result!.level) }]}
                xLabel={(x) => `set ${x}`}
                xTicks={trendX}
                unit=" ADU"
                height={160}
                extra={(_, i) => done[i]?.comment || null}
                ariaLabel="Level of each set's frames, by set"
              />
            </div>
            <div>
              <div className={styles.subTitle}>Gradient across the frame per set</div>
              <LineChart
                series={[{ key: "g", label: "Gradient", color: "var(--series-base)", x: trendX, y: done.map((s) => s.result!.structure.gradient.amp) }]}
                xLabel={(x) => `set ${x}`}
                xTicks={trendX}
                unit=" ADU"
                height={160}
                extra={(_, i) => (done[i] ? `toward the ${done[i].result!.structure.gradient.side}${done[i].comment ? ` · ${done[i].comment}` : ""}` : null)}
                ariaLabel="Gradient amplitude of each set's master, by set"
              />
            </div>
          </div>
        ) : null}
      </section>

      <section className={styles.block}>
        <div className={styles.blockHead}>
          <h2 className="section-title">{test.sets.length ? "Add the next set" : "Add the baseline"}</h2>
        </div>
        <p className={styles.note}>
          Same exposure, temperature and readout mode as the other sets, and the same light around the rig. A bright lamp or
          daylight on the rig makes leaks much easier to see than a dark room.
        </p>
        <AddSet test={test} onAdded={setTest} />
      </section>

      {done.length >= 2 && chosen && baseSet && compSet ? (
        <section className={styles.block}>
          <div className={styles.blockHead}>
            <h2 className="section-title">Comparison</h2>
          </div>
          <div className={styles.pickers}>
            <label className={styles.field}>
              <span>
                <span className={styles.swatch} style={{ background: "var(--series-base)" }} aria-hidden="true" />
                Baseline
              </span>
              <select className={styles.select} value={chosen.base} onChange={(e) => choose({ base: e.target.value, comp: chosen.comp })}>
                {done.map((s) => (
                  <option key={s.id} value={s.id}>
                    {setLabel(test, s.id)}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="button" onClick={() => choose({ base: chosen.comp, comp: chosen.base })} title="Swap baseline and comparison">
              <ArrowLeftRight size={13} strokeWidth={1.8} aria-hidden="true" /> Swap
            </button>
            <label className={styles.field}>
              <span>
                <span className={styles.swatch} style={{ background: "var(--series-comp)" }} aria-hidden="true" />
                Comparison
              </span>
              <select className={styles.select} value={chosen.comp} onChange={(e) => choose({ base: chosen.base, comp: e.target.value })}>
                {done.map((s) => (
                  <option key={s.id} value={s.id}>
                    {setLabel(test, s.id)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {chosen.base === chosen.comp ? (
            <p className="muted">Pick two different sets.</p>
          ) : (
            <Comparison test={test} base={baseSet} comp={compSet} />
          )}
        </section>
      ) : null}

      <section className={styles.block}>
        {confirmDelete ? (
          <div className={styles.row}>
            <span style={{ fontSize: 14 }}>Delete this test? Its sets and analyses go; the dark frames stay where they are.</span>
            <button
              type="button"
              className="button"
              onClick={async () => {
                await fetch(`/engine/api/leaks/${test.id}`, { method: "DELETE" });
                router.push("/library/calibration/leaks");
                router.refresh();
              }}
            >
              <Trash2 size={13} strokeWidth={1.8} aria-hidden="true" /> Delete
            </button>
            <button type="button" className={styles.link} onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className={styles.link} onClick={() => setConfirmDelete(true)}>
            <Trash2 size={12} strokeWidth={1.8} aria-hidden="true" /> Delete test
          </button>
        )}
      </section>
    </div>
  );
}
