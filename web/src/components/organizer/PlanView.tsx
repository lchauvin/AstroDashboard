"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { OP_LABEL, type OrgOp, type OrgPlan } from "@/lib/organizer";
import { Marks, StatusIcon } from "../ui";
import styles from "./organizer.module.css";

async function post(path: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

/** One plan: every operation, then approval (typing the id), the result, and undo. */
export function PlanView({ plan }: { plan: OrgPlan }) {
  const router = useRouter();
  const [typed, setTyped] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const strip = (p?: string) => (p && plan.target && p.startsWith(plan.target + "/") ? p.slice(plan.target.length + 1) : p ?? "");

  const groups = useMemo(() => {
    const g = new Map<string, { why: string; ops: { i: number; op: OrgOp }[] }>();
    plan.ops.forEach((op, i) => {
      const k = op.why || OP_LABEL[op.op];
      const e = g.get(k) ?? { why: k, ops: [] };
      e.ops.push({ i, op });
      g.set(k, e);
    });
    return [...g.values()];
  }, [plan]);

  const done = new Set(plan.result?.done ?? []);
  const needsAck = plan.warnings.length > 0;
  const canApply = plan.status === "draft" && typed.trim() === plan.id && (!needsAck || ack) && !busy;

  const run = async (fn: () => Promise<{ id: string }>, go = false) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (go) router.push(`/library/organizer/${encodeURIComponent(r.id)}`);
      else router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.plan}>
      {plan.conflicts.length ? (
        <div className={`framed ${styles.alert}`}>
          <Marks />
          <StatusIcon level="bad" size={15} />
          <div>
            <strong>Blocked: these destinations already exist, and nothing is ever overwritten.</strong>
            <ul className={styles.plain}>
              {plan.conflicts.map((c) => (
                <li key={c} className="mono">
                  {c}
                </li>
              ))}
            </ul>
            <p className={styles.hint}>Resolve them by hand (or tell me how you want them merged), then prepare a new plan.</p>
          </div>
        </div>
      ) : null}

      {plan.warnings.map((w) => (
        <div key={w} className={`framed ${styles.alert}`}>
          <Marks />
          <StatusIcon level="warn" size={15} />
          <p>{w}</p>
        </div>
      ))}

      {plan.lint.length ? (
        <section>
          <h2 className="section-title">What this fixes</h2>
          <ul className={styles.plain}>
            {plan.lint.map((l, i) => (
              <li key={i}>{l.text}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h2 className="section-title">
          {plan.ops.length} operation{plan.ops.length === 1 ? "" : "s"}, in order
        </h2>
        <p className={styles.hint}>
          Paths are relative to <span className="mono">{[plan.dataRoot ?? "the data root", plan.target].filter(Boolean).join("\\")}</span>. Folder moves are single
          renames on the same drive. Each step is re-checked just before it runs and journaled to <span className="mono">.Dashboard\journal.jsonl</span>.
        </p>
        {groups.map((g) => (
          <details key={g.why} className={styles.group} open={g.ops.length <= 12}>
            <summary>
              <b>{g.ops.length}</b> · {g.why}
            </summary>
            <table className="table">
              <tbody>
                {g.ops.map(({ i, op }) => (
                  <tr key={i} className={done.has(i) ? styles.done : undefined}>
                    <td className="num">{i + 1}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {done.has(i) ? <StatusIcon level="ok" size={12} /> : null} {OP_LABEL[op.op]}
                      {op.kind === "file" ? " (file)" : ""}
                    </td>
                    <td className={`mono ${styles.path}`}>
                      {op.src ? (
                        <>
                          {strip(op.src)} <span className={styles.arrow}>→</span> {op.op === "trash" ? op.dst : strip(op.dst)}
                        </>
                      ) : (
                        strip(op.dst)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ))}
      </section>

      {plan.result ? (
        <section className={`framed ${styles.resultBox}`}>
          <Marks />
          <StatusIcon level={plan.result.error ? "warn" : "ok"} size={15} />
          <div>
            <strong>
              {plan.result.done.length} of {plan.ops.length} operations applied.
            </strong>
            {plan.result.error ? <p className={styles.error}>Stopped: {plan.result.error}</p> : <p className={styles.hint}>The Library rescanned the new layout.</p>}
            {plan.result.done.length ? (
              <button type="button" className="button" disabled={busy} onClick={() => run(() => post(`/api/organizer/plans/${encodeURIComponent(plan.id)}/undo`), true)}>
                Prepare the undo plan
              </button>
            ) : null}
          </div>
        </section>
      ) : null}

      {plan.status === "draft" ? (
        <section className={`framed ${styles.approve}`}>
          <Marks />
          <h2 className="section-title">Approve</h2>
          <p>
            Nothing has moved yet. To apply these {plan.ops.length} operations to <b>{plan.dataRoot ?? "the data root"}</b>, type the plan id{" "}
            <span className={`mono ${styles.id}`}>{plan.id}</span>:
          </p>
          <input className={styles.input} value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={plan.id} spellCheck={false} aria-label="Plan id" />
          {needsAck ? (
            <label className={styles.ack}>
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I understand the PixInsight projects / WBPP folders above will need re-pointing.
            </label>
          ) : null}
          <div className={styles.row}>
            <button type="button" className="button button-primary" disabled={!canApply} onClick={() => run(() => post(`/api/organizer/plans/${encodeURIComponent(plan.id)}/apply`, { confirm: typed.trim() }))}>
              {busy ? "Applying…" : `Apply ${plan.ops.length} operations`}
            </button>
            <button type="button" className="button" disabled={busy} onClick={() => run(() => post(`/api/organizer/plans/${encodeURIComponent(plan.id)}/discard`))}>
              Discard draft
            </button>
          </div>
        </section>
      ) : null}
      {plan.status === "blocked" ? (
        <button type="button" className="button" disabled={busy} onClick={() => run(() => post(`/api/organizer/plans/${encodeURIComponent(plan.id)}/discard`))}>
          Discard
        </button>
      ) : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      <p>
        <Link href="/library/organizer">← All plans</Link>
      </p>
    </div>
  );
}
