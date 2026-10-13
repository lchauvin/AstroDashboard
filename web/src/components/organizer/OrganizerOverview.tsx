"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { FolderPlus } from "lucide-react";

import { stageLabel } from "@/lib/format";
import { OP_LABEL, bytes, type OrganizerOverview as Overview } from "@/lib/organizer";
import { StatusIcon, Tip } from "../ui";
import { NewTarget } from "./NewTarget";
import styles from "./organizer.module.css";

async function post(path: string, body: unknown) {
  const res = await fetch(`/engine${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

const STATUS_TEXT: Record<string, string> = {
  draft: "draft — waiting for your approval",
  blocked: "blocked by conflicts",
  applying: "applying…",
  applied: "applied",
  partial: "stopped part-way",
  failed: "failed before any change",
  discarded: "discarded",
};

const NEW_TARGET = "new-target";

/** Lint per revision, a new target, plans and the trash. Preparing a plan changes nothing on disk. */
export function OrganizerOverview({ data }: { data: Overview }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  // The error is shown next to what raised it: the new-target form, or the top of the page.
  const [error, setError] = useState<{ key: string; text: string } | null>(null);
  const make = async (path: string, body: unknown, key: string) => {
    setBusy(key);
    setError(null);
    try {
      const plan = await post(path, body);
      router.push(`/library/organizer/${encodeURIComponent(plan.id)}`);
    } catch (e) {
      setError({ key, text: e instanceof Error ? e.message : String(e) });
      setBusy(null);
    }
  };
  const open = data.revisions.filter((r) => !r.canonical);
  const known = new Set(data.revisions.map((r) => r.catalogId.toLowerCase()));

  return (
    <div className={styles.overview}>
      {error && error.key !== NEW_TARGET ? <p className={styles.error}>{error.text}</p> : null}
      <section>
        <h2 className="section-title">Layout lint</h2>
        <p className={styles.hint}>
          {open.length} of {data.revisions.length} revisions differ from the canonical layout. &ldquo;Prepare plan&rdquo; only writes
          a draft: you see every operation before anything moves, and applying needs you to type the plan id.
        </p>
        <div className={styles.scroller}>
          <table className={`table ${styles.lintTable}`}>
            <thead>
              <tr>
                <th>Revision</th>
                <th>Stage</th>
                <th>Findings</th>
                <th className="num">Operations</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {data.revisions.map((r) => {
                const byCode = new Map<string, number>();
                for (const l of r.lint) byCode.set(l.code, (byCode.get(l.code) ?? 0) + 1);
                const nOps = Object.values(r.counts).reduce((a, b) => a + b, 0);
                return (
                  <tr key={r.revisionId}>
                    <td>
                      <Link href={`/library/${r.revisionId}`} className={styles.strong}>
                        {r.folder}
                      </Link>
                    </td>
                    <td>{stageLabel(r.stage)}</td>
                    <td>
                      {r.canonical ? (
                        <StatusIcon level="ok" size={13} label="Canonical layout" />
                      ) : (
                        <Tip
                          tip={
                            <span className={styles.tipList}>
                              {[...byCode.entries()].map(([code, n]) => (
                                <span key={code}>
                                  {LINT_LABEL[code] ?? code}
                                  {n > 1 ? ` ×${n}` : ""}
                                </span>
                              ))}
                              {r.conflicts.map((c) => (
                                <span key={c} className={styles.tipBad}>
                                  {c}
                                </span>
                              ))}
                              {r.warnings.map((w) => (
                                <span key={w} className="muted">
                                  {w}
                                </span>
                              ))}
                            </span>
                          }
                        >
                          <span className={styles.summary}>
                            {r.conflicts.length ? (
                              <span className={styles.summary}>
                                <StatusIcon level="bad" size={13} label="Conflicts" />
                                <span className="num">{r.conflicts.length}</span>
                              </span>
                            ) : null}
                            <StatusIcon level="warn" size={13} label="Layout findings" />
                            <span className="num">{byCode.size}</span>
                            <span className="muted">{byCode.size === 1 ? "issue" : "issues"}</span>
                          </span>
                        </Tip>
                      )}
                    </td>
                    <td className="num">
                      {nOps ? (
                        <Tip
                          tip={
                            <span className={styles.tipList}>
                              {Object.entries(r.counts).map(([k, v]) => (
                                <span key={k}>
                                  {OP_LABEL[k as keyof typeof OP_LABEL] ?? k}: {v}
                                </span>
                              ))}
                            </span>
                          }
                        >
                          <span>{nOps}</span>
                        </Tip>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className={styles.actions}>
                      {!r.canonical ? (
                        <button type="button" className={styles.action} disabled={busy !== null} onClick={() => make("/api/organizer/plans", { revisionId: r.revisionId }, r.revisionId)}>
                          {busy === r.revisionId ? "Preparing…" : "Prepare plan"}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className={styles.iconAction}
                        disabled={busy !== null}
                        onClick={() => make("/api/organizer/new-revision", { revisionId: r.revisionId }, r.revisionId)}
                        title="New revision: creates <Target> R<n+1> with an empty .SessionData and a target.json (after approval). Moves nothing."
                        aria-label="New revision"
                      >
                        <FolderPlus size={13} strokeWidth={1.8} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="section-title">New target</h2>
        <p className={styles.hint}>
          Creates the canonical folders and a target.json for revision 1, after you approve the plan. Ingest sends frames to it
          once a NINA object name matches its catalog id or name.
        </p>
        <NewTarget known={known} dataRoot={data.dataRoot} busy={busy !== null} onPrepare={(body) => make("/api/organizer/new-target", body, NEW_TARGET)} />
        {error?.key === NEW_TARGET ? <p className={styles.error}>{error.text}</p> : null}
      </section>

      <section>
        <h2 className="section-title">Plans</h2>
        {data.plans.length ? (
          <table className="table">
            <tbody>
              {data.plans.map((p) => (
                <tr key={p.id}>
                  <td>
                    <Link href={`/library/organizer/${encodeURIComponent(p.id)}`} className={styles.strong}>
                      {p.title}
                    </Link>
                    <div className={`mono ${styles.hint}`}>{p.id}</div>
                  </td>
                  <td>{STATUS_TEXT[p.status] ?? p.status}</td>
                  <td className="num">{p.counts ? Object.values(p.counts).reduce((a, b) => a + b, 0) : 0} ops</td>
                  <td className={styles.hint}>{new Date(p.created * 1000).toLocaleString("en-CA")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className={styles.hint}>No plans yet.</p>
        )}
      </section>

      <section>
        <h2 className="section-title">Trash</h2>
        <p className={styles.hint}>
          <span className="mono">{data.trash.path}</span> — {data.trash.files} file{data.trash.files === 1 ? "" : "s"}, {bytes(data.trash.bytes)}.
          Each plan&apos;s trash keeps the original relative paths, so it can be put back exactly. Emptying it is up to you,
          outside the dashboard.
        </p>
        {data.trash.plans.length ? (
          <ul className={styles.plain}>
            {data.trash.plans.map((t) => (
              <li key={t.plan}>
                <span className="mono">{t.plan}</span> · {t.files} files · {bytes(t.bytes)}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}

const LINT_LABEL: Record<string, string> = {
  year: "year level",
  years: "several years",
  "session-name": "session names",
  flagged: "legacy sessions",
  flatwizard: "FlatWizard",
  "object-folder": "object folders",
  panel: "panel folders",
  "empty-stage": "empty 1.–7.",
  nonconforming: "non-conforming",
  pxiproject: "PixInsight projects",
};
