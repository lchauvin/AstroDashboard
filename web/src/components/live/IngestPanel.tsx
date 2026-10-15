"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { fileName, type IngestAction, type IngestHold, type IngestStatus } from "@/lib/live";
import { relativeTime } from "@/lib/format";
import { FilterChip, Marks, StatusIcon } from "../ui";
import styles from "./live.module.css";

const MODES = [
  { id: "off", label: "Off", note: "Nothing is scanned." },
  { id: "dry-run", label: "Dry-run", note: "Shows what would move; nothing moves." },
  { id: "auto", label: "Auto", note: "Moves finished frames into their target's NIGHT_ folder." },
] as const;

async function post(path: string, body?: unknown, method = "POST") {
  const res = await fetch(`/engine${path}`, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

/** Auto-ingest from .Download (PLAN §5.1): mode, plan per night, held frames, and the journal with undo. */
export function IngestPanel({ initial }: { initial: IngestStatus | null }) {
  const [st, setSt] = useState<IngestStatus | null>(initial);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null); // "undo:<night>" | "cleanup:<night>" | "copies:<night>"
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/engine/api/ingest", { cache: "no-store" });
      if (res.ok) setSt(await res.json());
    } catch {}
  }, []);
  useEffect(() => {
    const id = setInterval(refresh, 10_000);
    return () => clearInterval(id);
  }, [refresh]);

  const act = async (fn: () => Promise<unknown>, done?: (r: unknown) => string | null) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      setMsg(done ? done(r) : null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  };

  const nights = useMemo(() => {
    const names = new Set<string>([...Object.keys(st?.plan?.nights ?? {}), ...Object.keys(st?.journal ?? {})]);
    return [...names].sort().reverse();
  }, [st]);

  if (!st) return <p className="muted">The engine is not reachable.</p>;
  const mode = st.mode;

  return (
    <div className={styles.ingest}>
      <div className={styles.ingestHead}>
        <div className={styles.modes} role="group" aria-label="Auto-ingest mode">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              className={mode === m.id ? `${styles.mode} ${styles.modeOn}` : styles.mode}
              aria-pressed={mode === m.id}
              disabled={busy}
              onClick={() => (m.id === "auto" && mode !== "auto" ? setConfirmAuto(true) : act(() => post("/api/ingest/mode", { mode: m.id })))}
              title={m.note}
            >
              {m.label}
            </button>
          ))}
        </div>
        <span className={styles.hint}>
          {MODES.find((m) => m.id === mode)?.note} Watching <span className="mono">{st.downloadRoot}</span>
          {!st.downloadExists ? " (does not exist)" : ""}
          {st.lastRun ? ` · checked ${relativeTime(new Date(st.lastRun.at * 1000).toISOString())}` : " · first check pending"}
        </span>
        <button type="button" className="button" disabled={busy} onClick={() => act(() => post("/api/ingest/run"), (r) => {
          const x = r as { moved: number; copied: number; errors: number };
          return mode === "auto" ? `Moved ${x.moved}, copied ${x.copied}${x.errors ? `, ${x.errors} errors` : ""}.` : "Plan refreshed.";
        })}>
          Check now
        </button>
      </div>

      {confirmAuto ? (
        <div className={`framed ${styles.confirmBox}`}>
          <Marks />
          <strong>Switch auto-ingest on?</strong>
          <p>
            Finished frames will be <b>moved</b> from <span className="mono">.Download</span> into each target&apos;s{" "}
            <span className="mono">.SessionData\NIGHT_&lt;date&gt;</span> as they land: same-drive renames, never overwriting,
            never deleting, every move journaled and undoable per night. Unknown objects, conflicts and incomplete files stay put.
            Watch one real night in Dry-run first.
          </p>
          <div className={styles.row}>
            <button type="button" className="button button-primary" onClick={() => { setConfirmAuto(false); act(() => post("/api/ingest/mode", { mode: "auto" })); }}>
              Yes, switch to Auto
            </button>
            <button type="button" className="button" onClick={() => setConfirmAuto(false)}>
              Stay in {mode === "off" ? "Off" : "Dry-run"}
            </button>
          </div>
        </div>
      ) : null}

      {msg ? <p className={styles.hint}>{msg}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      {st.error ? <p className={styles.error}>Last pass failed: {st.error}</p> : null}

      {!nights.length ? (
        <p className={styles.hint}>
          Nothing in <span className="mono">.Download</span> and nothing ingested yet. NINA&apos;s frames appear here as they land.
        </p>
      ) : null}

      {nights.map((n) => (
        <Night
          key={n}
          name={n}
          st={st}
          busy={busy}
          confirm={confirm}
          setConfirm={setConfirm}
          act={act}
        />
      ))}
    </div>
  );
}

function groupActions(actions: IngestAction[]) {
  const g = new Map<string, { target: string; night: string; filter: string | null; kind: string; op: string; n: number; files: IngestAction[] }>();
  for (const a of actions) {
    const k = `${a.target}|${a.night}|${a.kind}|${a.filter}|${a.op}`;
    const e = g.get(k) ?? { target: a.target, night: a.night, filter: a.filter, kind: a.kind, op: a.op, n: 0, files: [] };
    e.n++;
    e.files.push(a);
    g.set(k, e);
  }
  return [...g.values()];
}

function Night({
  name,
  st,
  busy,
  confirm,
  setConfirm,
  act,
}: {
  name: string;
  st: IngestStatus;
  busy: boolean;
  confirm: string | null;
  setConfirm: (c: string | null) => void;
  act: (fn: () => Promise<unknown>, done?: (r: unknown) => string | null) => Promise<void>;
}) {
  const plan = st.plan;
  const info = plan?.nights[name];
  const actions = (plan?.actions ?? []).filter((a) => a.download_night === name);
  const holds = (plan?.holds ?? []).filter((h) => h.download_night === name);
  const skipped = (plan?.skipped ?? []).filter((h) => h.download_night === name);
  const j = st.journal[name];
  const closed = st.closed.includes(name);
  const paused = st.paused.includes(name);
  const prompts = new Map<string, IngestHold[]>();
  for (const h of holds) if (h.prompt && h.object) prompts.set(h.object, [...(prompts.get(h.object) ?? []), h]);
  const byReason = new Map<string, IngestHold[]>();
  for (const h of holds) if (!h.prompt) byReason.set(h.reason, [...(byReason.get(h.reason) ?? []), h]);
  const emptyDirs = info?.emptyDirs ?? j?.emptyDirs ?? 0;
  const ingested = info?.ingested ?? 0;

  return (
    <section className={`framed ${styles.night}`}>
      <Marks />
      <div className={styles.nightHead}>
        <h3 className={styles.nightTitle}>
          {info && info.folder === false ? (
            <>
              Night of {name} <span className={styles.hint}>· frames directly under .Download (no night folder)</span>
            </>
          ) : (
            <span className="mono">.Download\{name}</span>
          )}
        </h3>
        <span className={styles.hint}>
          {info ? `${info.lights + info.flats - ingested ? `${info.lights + info.flats - ingested} waiting` : "nothing waiting"}${ingested ? ` · ${ingested} already ingested` : ""}` : "nothing left waiting"} ·{" "}
          {closed ? "night closed" : "night open (newest light kept for the preview)"}
        </span>
        <div className={styles.row}>
          {!closed && info ? (
            <button type="button" className="button" disabled={busy} onClick={() => act(() => post("/api/ingest/close", { night: name }), () => `Closed ${name}: the newest light can move too.`)}>
              Close night
            </button>
          ) : null}
          {j && j.moved > 0 ? (
            confirm === `undo:${name}` ? (
              <span className={styles.confirmInline}>
                Move {j.moved} frame{j.moved === 1 ? "" : "s"} back into .Download? The night is then paused, so Auto does not redo it.{" "}
                <button type="button" className={styles.link} onClick={() => act(() => post("/api/ingest/undo", { night: name }), (r) => {
                  const x = r as { movedBack: number; copiesLeft: string[]; failed: unknown[] };
                  return `Moved ${x.movedBack} back.${x.copiesLeft.length ? ` ${x.copiesLeft.length} flat cop${x.copiesLeft.length === 1 ? "y was" : "ies were"} left in place (removing ${x.copiesLeft.length === 1 ? "it" : "them"} would be a deletion).` : ""}${x.failed.length ? ` ${x.failed.length} could not be moved back.` : ""}`;
                })}>
                  Yes, undo
                </button>{" "}
                <button type="button" className={styles.link} onClick={() => setConfirm(null)}>
                  Cancel
                </button>
              </span>
            ) : (
              <button type="button" className="button" disabled={busy} onClick={() => setConfirm(`undo:${name}`)}>
                Undo night
              </button>
            )
          ) : null}
          {ingested > 0 ? (
            confirm === `copies:${name}` ? (
              <span className={styles.confirmInline}>
                Remove the {ingested} cop{ingested === 1 ? "y" : "ies"} in .Download of frames already in their session? Each is compared byte for byte
                with the ingested frame first; anything that differs stays.{" "}
                <button type="button" className={styles.link} onClick={() => act(() => post("/api/ingest/remove-copies", { night: name, confirm: true }), (r) => {
                  const x = r as { removed: number; kept: { src: string; error: string }[] };
                  return `Removed ${x.removed} cop${x.removed === 1 ? "y" : "ies"}.${x.kept.length ? ` ${x.kept.length} kept (not identical or not found): e.g. ${fileName(x.kept[0].src)}.` : ""}`;
                })}>
                  Yes, remove
                </button>{" "}
                <button type="button" className={styles.link} onClick={() => setConfirm(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <button type="button" className="button" disabled={busy} onClick={() => setConfirm(`copies:${name}`)}>
                Remove {ingested} cop{ingested === 1 ? "y" : "ies"} already ingested…
              </button>
            )
          ) : null}
          {emptyDirs > 0 ? (
            confirm === `cleanup:${name}` ? (
              <span className={styles.confirmInline}>
                Remove {emptyDirs} empty folder{emptyDirs === 1 ? "" : "s"} in .Download\{name}? Only empty folders are removed.{" "}
                <button type="button" className={styles.link} onClick={() => act(() => post("/api/ingest/cleanup", { night: name, confirm: true }), (r) => `Removed ${(r as { removed: string[] }).removed.length} empty folders.`)}>
                  Yes, remove
                </button>{" "}
                <button type="button" className={styles.link} onClick={() => setConfirm(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <button type="button" className="button" disabled={busy} onClick={() => setConfirm(`cleanup:${name}`)}>
                Remove {emptyDirs} empty folder{emptyDirs === 1 ? "" : "s"}…
              </button>
            )
          ) : null}
        </div>
      </div>

      {paused ? (
        <div className={styles.prompt}>
          <StatusIcon level="warn" size={14} />
          <div>
            <b>Paused after undo.</b> Auto leaves this night alone, so the undo is not redone on the next pass.{" "}
            <button type="button" className={styles.link} disabled={busy} onClick={() => act(() => post("/api/ingest/resume", { night: name }), () => `Resumed ${name}.`)}>
              Resume ingest for this night
            </button>
          </div>
        </div>
      ) : null}

      {[...prompts.entries()].map(([object, hs]) => (
        <MapPrompt key={object} object={object} holds={hs} st={st} act={act} />
      ))}

      {actions.length ? (
        <div className={styles.block}>
          <div className="eyebrow">{st.mode === "auto" && !paused ? "Moving next" : paused ? "Would move (paused)" : "Would move (dry-run)"}</div>
          <table className="table">
            <tbody>
              {groupActions(actions).map((g) => (
                <tr key={`${g.target}${g.night}${g.kind}${g.filter}${g.op}`}>
                  <td className="num">{g.n}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {g.kind === "FLAT" ? "flat" : "light"}
                    {g.n === 1 ? "" : "s"} <FilterChip filter={g.filter} />
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {g.op === "copy" ? "copy to" : "→"} <span className="mono">{g.target}\…\{g.night}</span>
                  </td>
                  <td className={styles.hint} title={g.files.map((f) => fileName(f.src)).join("\n")}>
                    {fileName(g.files[0].src)}
                    {g.n > 1 ? ` … ${fileName(g.files[g.n - 1].src)}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {byReason.size ? (
        <div className={styles.block}>
          <div className="eyebrow">Held back</div>
          <ul className={styles.holds}>
            {[...byReason.entries()].map(([reason, hs]) => (
              <li key={reason}>
                <StatusIcon level={reason === "newest" || reason === "settling" ? "ok" : reason === "ingested" ? "info" : "warn"} size={13} />
                <span>
                  <b>{hs.length}</b> ·{" "}
                  {reason === "ingested"
                    ? "already ingested: a copy came back into .Download after the frame was moved (robocopy re-syncing the observatory PC). The frame is safe in its session."
                    : hs[0].message}
                  {hs.length > 1 ? <span className={styles.hint}> (e.g. {fileName(hs[0].src)})</span> : <span className={styles.hint}> {fileName(hs[0].src)}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {skipped.length ? <p className={styles.hint}>{skipped.length} file{skipped.length === 1 ? "" : "s"} left in place by design (darks, biases, snapshots, guide images).</p> : null}

      {j ? (
        <p className={styles.hint}>
          Journal: {j.moved} moved, {j.copied} copied{j.undone ? `, ${j.undone} undone` : ""}
          {j.errors ? `, ${j.errors} failed` : ""} · last {relativeTime(new Date(j.last * 1000).toISOString())}.{" "}
          {j.moved ? <Link href="/diagnostics">Analyse the new sessions in Diagnostics →</Link> : null}
        </p>
      ) : null}
    </section>
  );
}

function MapPrompt({ object, holds, st, act }: { object: string; holds: IngestHold[]; st: IngestStatus; act: (fn: () => Promise<unknown>, done?: (r: unknown) => string | null) => Promise<void> }) {
  const choices = st.targets;
  const [rev, setRev] = useState(choices.find((t) => t.stage === "acquiring")?.id ?? choices[0]?.id ?? "");
  return (
    <div className={styles.prompt}>
      <StatusIcon level="warn" size={14} />
      <div>
        <div>
          <b>{holds.length}</b> frame{holds.length === 1 ? "" : "s"} of <b>“{object}”</b> waiting: {holds[0].message}
        </div>
        <div className={styles.row} style={{ marginTop: 8 }}>
          <label className={styles.hint}>
            Send “{object}” to{" "}
            <select className={styles.select} value={rev} onChange={(e) => setRev(e.target.value)}>
              {choices.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.folder} — {t.stage}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="button button-primary" disabled={!rev} onClick={() => act(() => post("/api/ingest/map", { object, revisionId: rev }, "PUT"), () => `“${object}” now goes to ${choices.find((t) => t.id === rev)?.folder}. Remembered for next time.`)}>
            Remember &amp; ingest
          </button>
          <span className={styles.hint}>A finished target needs a new revision folder (organizer, Phase 6) — or pick its revision explicitly.</span>
        </div>
      </div>
    </div>
  );
}
