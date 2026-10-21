"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { clock, downloadName, enginePost, type IngestObject, type IngestStatus, type IngestTarget } from "@/lib/live";
import { AlertTriangle } from "lucide-react";

import { Marks, StatusIcon, Tip } from "../ui";
import styles from "./live.module.css";

const STAGE_ORDER = ["acquiring", "processed", "finished"];

/** A warning shown as its icon only; the message appears on hover (or keyboard focus). */
function WarnTip({ children }: { children: React.ReactNode }) {
  return (
    <Tip side="left" tip={<span className={styles.warnTip}>{children}</span>}>
      <span className={styles.warnIcon}>
        <AlertTriangle size={14} strokeWidth={1.8} color="var(--status-warn)" aria-hidden="true" />
        <span className="sr-only">Warning</span>
      </span>
    </Tip>
  );
}

const nightLabel = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" });

type Pending = { revisionId: string | null; object: string | null };

/**
 * Tonight's target. By default it follows NINA: each NINA target goes to its library target (several in a
 * night each go to their own). Finished targets are skipped with a warning unless "also switch to finished
 * targets" is on, which also marks them Acquiring again. Choosing a target yourself sends every frame of the
 * night there and pauses following. Auto-ingest does the moving, into <target>\.SessionData\NIGHT_<date>.
 */
export function TonightTarget({ initial }: { initial: IngestStatus | null }) {
  const [st, setSt] = useState<IngestStatus | null>(initial);
  const [pick, setPick] = useState("");
  const [confirm, setConfirm] = useState<Pending | null>(null);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [confirmFinished, setConfirmFinished] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/engine/api/ingest", { cache: "no-store" });
      if (res.ok) setSt(await res.json());
    } catch {}
  }, []);
  useEffect(() => {
    const id = setInterval(() => !document.hidden && void refresh(), 8000);
    return () => clearInterval(id);
  }, [refresh]);

  const targets = st?.targets ?? [];
  const byId = useMemo(() => new Map(targets.map((t) => [t.id, t])), [targets]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      // The ingest thread re-plans at once; read again after a moment for the new plan.
      setTimeout(() => void refresh(), 1200);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const send = (p: Pending, confirmed: boolean) =>
    run(async () => {
      await enginePost("/api/ingest/tonight", { revisionId: p.revisionId, object: p.object, confirm: confirmed });
      setConfirm(null);
      setPick("");
    });

  /** Choosing a revision that is not acquiring asks first. */
  const choose = (p: Pending) => {
    const t = p.revisionId ? byId.get(p.revisionId) : null;
    if (t && t.stage !== "acquiring") setConfirm(p);
    else void send(p, false);
  };

  const setFollow = (body: { follow?: boolean; finished?: boolean }) =>
    run(async () => {
      await enginePost("/api/ingest/follow", body);
      setConfirmFinished(false);
    });

  if (!st) {
    return (
      <section className={`framed ${styles.card}`}>
        <Marks />
        <span className="eyebrow">Tonight&apos;s target</span>
        <p className={styles.hint}>The library is still being scanned; targets appear once it is ready.</p>
      </section>
    );
  }

  const tonight = st.tonight;
  const a = st.assignments?.[tonight];
  const manual = a?.all ? byId.get(a.all) ?? null : null;
  const objects: IngestObject[] = Object.values(st.plan?.objects?.[tonight] ?? {}).sort((x, y) => y.frames - x.frames);
  const nina = st.nina;
  const reopenedTonight = (st.reopened ?? []).filter((r) => r.ts > Date.now() / 1000 - 16 * 3600);
  const confirmTarget = confirm?.revisionId ? byId.get(confirm.revisionId) : null;
  const showObjects = objects.length > 1 || objects.some((o) => !o.revisionId) || Object.keys(a?.objects ?? {}).length > 0;
  const moved = (rid: string | null | undefined) => (rid ? st.tonightMoved?.[rid] ?? 0 : 0);

  return (
    <section className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.cardHead}>
        <span className="eyebrow">Tonight&apos;s target · {nightLabel(tonight)}</span>
        <span className={styles.state}>
          {st.mode === "auto" ? (
            <StatusIcon level="ok" size={13} />
          ) : (
            <WarnTip>Ingest is {st.mode === "off" ? "off" : "in dry-run"}: frames are listed but nothing moves until you switch to Auto.</WarnTip>
          )}{" "}
          Ingest {st.mode}
          {st.mode !== "auto" && !confirmAuto ? (
            <button type="button" className={styles.link} onClick={() => setConfirmAuto(true)}>
              Switch to Auto…
            </button>
          ) : null}
        </span>
      </div>

      {manual ? (
        <>
          <div className={styles.tonightName}>
            {manual.folder}
            {manual.stage !== "acquiring" ? (
              <WarnTip>
                Marked “{manual.stage}”: frames you send here are not part of its {manual.stage === "finished" ? "finished image" : "processed integration"}.
              </WarnTip>
            ) : null}
          </div>
          <p className={styles.hint}>
            Your choice: every light of tonight goes here, whatever NINA is imaging. {moved(manual.id)} moved tonight. Following NINA is paused.
          </p>
          <div className={styles.row}>
            <button type="button" className="button" disabled={busy} onClick={() => void setFollow({ follow: true })}>
              Follow NINA instead
            </button>
          </div>
        </>
      ) : (
        <>
          <label className={styles.switch}>
            <input type="checkbox" checked={st.follow} disabled={busy} onChange={(e) => void setFollow({ follow: e.target.checked })} />
            <span>Follow NINA&apos;s target</span>
          </label>
          {st.follow ? (
            <NinaLine st={st} busy={busy} moved={moved} targets={targets} onChoose={(rid) => choose({ revisionId: rid, object: nina?.object ?? null })} />
          ) : (
            <p className={styles.hint}>Not following NINA: frames go by their object name through the mapping table (Settings → Live · Ingest), acquiring targets only.</p>
          )}
          {st.follow ? (
            <>
              <label className={styles.switch}>
                <input
                  type="checkbox"
                  checked={st.followFinished}
                  disabled={busy}
                  onChange={(e) => (e.target.checked ? setConfirmFinished(true) : void setFollow({ finished: false }))}
                />
                <span>Also switch to finished targets</span>
                {st.followFinished && !confirmFinished ? (
                  <WarnTip>On: when NINA images a finished target, it is marked Acquiring again and receives its frames.</WarnTip>
                ) : null}
              </label>
              {confirmFinished ? (
                <div className={styles.warnBox} role="alert">
                  <StatusIcon level="warn" size={15} />
                  <div>
                    <p>
                      When NINA images a target that is marked <b>Finished</b>, its frames will go there anyway and the target will be marked{" "}
                      <b>Acquiring</b> again (its <span className="mono">target.json</span> status is changed when the first frame moves, and the change
                      is listed here with a button to undo it). Its finished image will no longer match its data.
                    </p>
                    <div className={styles.row}>
                      <button type="button" className="button" disabled={busy} onClick={() => void setFollow({ finished: true })}>
                        Turn on
                      </button>
                      <button type="button" className={styles.link} onClick={() => setConfirmFinished(false)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          ) : null}
        </>
      )}

      {reopenedTonight.length ? (
        <ul className={styles.reopenList}>
          {reopenedTonight.map((r) => (
            <li key={`${r.ts}-${r.revisionId}`}>
              <span className={styles.inlineWarn}>
                <WarnTip>
                  {clock(r.ts)} · {r.folder}: status changed from “{r.from}” to “{r.to}” in its target.json.
                </WarnTip>
                {r.to === "acquiring" ? "Reopened" : "Marked finished"} {r.folder}
              </span>
              {r.to === "acquiring" && byId.get(r.revisionId)?.stage === "acquiring" ? (
                <button type="button" className={styles.link} disabled={busy}
                  onClick={() => void run(() => enginePost("/api/ingest/status", { revisionId: r.revisionId, status: "finished" }))}>
                  Mark finished again
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* Choosing by hand only when not following NINA (or when a manual choice is already in place). */}
      {manual || !st.follow ? (
        <div className={styles.pickRow}>
          <TargetSelect targets={targets} value={pick} onChange={setPick} label={manual ? "Change target" : "Choose a target"} />
          <button type="button" className="button button-primary" disabled={busy || !pick || pick === manual?.id} onClick={() => choose({ revisionId: pick, object: null })}>
            {manual ? "Change" : "Use"}
          </button>
        </div>
      ) : null}

      {confirm && confirmTarget ? (
        <div className={styles.warnBox} role="alert">
          <StatusIcon level="warn" size={15} />
          <div>
            <strong>
              {confirmTarget.folder} is marked “{confirmTarget.stage}”.
            </strong>
            <p>
              {confirmTarget.stage === "finished"
                ? "Frames added now will not be part of its finished image, and they reopen a data set you closed."
                : "It has already been processed: new frames will not be in that integration."}{" "}
              To start a new data set for this object, create a new revision in <Link href="/library/organizer">Library › Organizer</Link> and pick it here.
            </p>
            <div className={styles.row}>
              <button type="button" className="button" disabled={busy} onClick={() => void send(confirm, true)}>
                Send {confirm.object ? `“${confirm.object}”` : "tonight’s frames"} there anyway
              </button>
              <button type="button" className={styles.link} onClick={() => setConfirm(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {st.mode !== "auto" && confirmAuto ? (
        <div className={styles.warnBox}>
          <StatusIcon level="info" size={15} />
          <div>
            <p>
              Auto moves finished frames from <span className="mono">{downloadName(st)}</span> into each target&apos;s <span className="mono">NIGHT_</span> folder
              (moves only, never overwrites; the newest frame stays for the preview). Every move is journaled and each night can be undone below.
            </p>
            <div className={styles.row}>
              <button type="button" className="button button-primary" disabled={busy}
                onClick={() => void run(async () => { await enginePost("/api/ingest/mode", { mode: "auto" }); setConfirmAuto(false); })}>
                Switch to Auto
              </button>
              <button type="button" className={styles.link} onClick={() => setConfirmAuto(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showObjects ? (
        <details className={styles.older} open={objects.some((o) => !o.revisionId)}>
          <summary>
            NINA targets tonight ({objects.length}){objects.some((o) => !o.revisionId) ? " · some have no target" : ""}
          </summary>
          <ul className={styles.objList}>
            {objects.map((o) => (
              <ObjectRow key={o.object ?? ""} o={o} targets={targets} overridden={!!(o.object && a?.objects?.[o.object.toLowerCase()])} busy={busy}
                onChoose={(rid) => choose({ revisionId: rid, object: o.object })} onClear={() => void send({ revisionId: null, object: o.object }, false)} />
            ))}
          </ul>
        </details>
      ) : null}

      {error ? <p className={styles.error}>{error}</p> : null}
    </section>
  );
}

/** What NINA is imaging and where following it sends the frames — or why it does not. */
function NinaLine({ st, busy, moved, targets, onChoose }: { st: IngestStatus; busy: boolean; moved: (rid: string | null) => number; targets: IngestTarget[]; onChoose: (rid: string) => void }) {
  const [pick, setPick] = useState("");
  const n = st.nina;
  if (!n) return <p className={styles.hint}>NINA has no target running; frames will follow its target once the sequence reaches one.</p>;
  const pending = st.plan?.reopen?.some((r) => r.revisionId === n.revisionId);
  if (n.revisionId) {
    return (
      <div className={styles.ninaLine}>
        <div className={styles.tonightName}>
          {n.target}
          {n.stage !== "acquiring" ? (
            <WarnTip>
              Marked “{n.stage}”
              {n.stage === "finished"
                ? pending
                  ? ": it will be marked Acquiring when its frames move."
                  : ": frames go there because “Also switch to finished targets” is on."
                : "."}
            </WarnTip>
          ) : null}
        </div>
        <p className={styles.hint}>
          NINA {n.running ? "is imaging" : "last targeted"} <b>{n.object}</b> · {moved(n.revisionId)} moved tonight
        </p>
      </div>
    );
  }
  if (n.blockedBy) {
    return (
      <div className={styles.ninaLine}>
        <div className={styles.tonightName}>
          {n.target}
          <WarnTip>
            Marked “{n.stage}”: not switching to it. NINA&apos;s frames of {n.object} stay in {downloadName(st)} until you turn on “Also switch to finished
            targets”, turn off “Follow NINA&apos;s target” to choose one yourself, or start a new revision in Library › Organizer.
          </WarnTip>
        </div>
        <p className={styles.hint}>
          NINA is imaging <b>{n.object}</b> · not switched (marked {n.stage})
        </p>
      </div>
    );
  }
  return (
    <div className={styles.ninaLine}>
      <p className={styles.inlineWarn}>
        <WarnTip>No target in the library matches the NINA target “{n.object}”. Pick one for it here, or create it in Library › Organizer.</WarnTip>
        <span>
          NINA is imaging <b>{n.object}</b> · no matching target
        </span>
      </p>
      <div className={styles.row}>
        <TargetSelect targets={targets} value={pick} onChange={setPick} label={`Send “${n.object}” to`} />
        <button type="button" className="button" disabled={busy || !pick} onClick={() => onChoose(pick)}>
          Set
        </button>
      </div>
    </div>
  );
}

function TargetSelect({ targets, value, onChange, label }: { targets: IngestTarget[]; value: string; onChange: (v: string) => void; label: string }) {
  const groups = [...STAGE_ORDER, ...Array.from(new Set(targets.map((t) => t.stage))).filter((s) => !STAGE_ORDER.includes(s))];
  return (
    <select className={styles.select} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{label}…</option>
      {groups.map((g) => {
        const list = targets.filter((t) => t.stage === g).sort((x, y) => x.folder.localeCompare(y.folder));
        return list.length ? (
          <optgroup key={g} label={g === "acquiring" ? "Acquiring" : g === "finished" ? "Finished (asks first)" : `${g[0].toUpperCase()}${g.slice(1)} (asks first)`}>
            {list.map((t) => (
              <option key={t.id} value={t.id}>
                {t.folder}
              </option>
            ))}
          </optgroup>
        ) : null;
      })}
    </select>
  );
}

const HOW: Record<string, string> = { tonight: "tonight's target", follow: "follows NINA", mapping: "mapping table", name: "name match" };

function ObjectRow({ o, targets, overridden, busy, onChoose, onClear }: { o: IngestObject; targets: IngestTarget[]; overridden: boolean; busy: boolean; onChoose: (rid: string) => void; onClear: () => void }) {
  const [pick, setPick] = useState("");
  const how = o.how === "tonight" && overridden ? "chosen for this target" : HOW[o.how] ?? "";
  return (
    <li>
      <div>
        <b>{o.object ?? "(no object name)"}</b> <span className={styles.hint}>· {o.frames} waiting</span>
        <div className={styles.hint}>
          {o.revisionId ? (
            <>
              → {o.target}
              {o.stage && o.stage !== "acquiring" ? ` (${o.stage})` : ""} · {how}
            </>
          ) : (
            <span className={styles.inlineWarn}>
              <WarnTip>{o.problem || "No target"}</WarnTip> no target
            </span>
          )}
        </div>
      </div>
      {o.object ? (
        <span className={styles.row}>
          <TargetSelect targets={targets} value={pick} onChange={setPick} label="Send to" />
          <button type="button" className="button" disabled={busy || !pick} onClick={() => onChoose(pick)}>
            Set
          </button>
          {overridden ? (
            <button type="button" className={styles.link} disabled={busy} onClick={onClear}>
              Clear
            </button>
          ) : null}
        </span>
      ) : null}
    </li>
  );
}
