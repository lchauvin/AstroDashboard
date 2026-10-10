"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { stageLabel } from "@/lib/format";
import { bytes, type OrganizerOverview as Overview } from "@/lib/organizer";
import { StatusIcon } from "../ui";
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

/** Lint per revision, plans and the trash. Preparing a plan changes nothing on disk. */
export function OrganizerOverview({ data }: { data: Overview }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const make = async (path: string, revisionId: string) => {
    setBusy(revisionId);
    setError(null);
    try {
      const plan = await post(path, { revisionId });
      router.push(`/library/organizer/${encodeURIComponent(plan.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };
  const open = data.revisions.filter((r) => !r.canonical);

  return (
    <div className={styles.overview}>
      {error ? <p className={styles.error}>{error}</p> : null}
      <section>
        <h2 className="section-title">Layout lint</h2>
        <p className={styles.hint}>
          {open.length} of {data.revisions.length} revisions differ from the canonical layout. &ldquo;Prepare plan&rdquo; only writes
          a draft: you see every operation before anything moves, and applying needs you to type the plan id.
        </p>
        <div className={styles.scroller}>
          <table className="table">
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
                        <span className={styles.ok}>
                          <StatusIcon level="ok" size={13} /> canonical
                        </span>
                      ) : (
                        <span className={styles.chips}>
                          {[...byCode.entries()].map(([code, n]) => (
                            <span key={code} className={styles.chip} title={r.lint.filter((l) => l.code === code).map((l) => l.text).join("\n")}>
                              {LINT_LABEL[code] ?? code}
                              {n > 1 ? ` ×${n}` : ""}
                            </span>
                          ))}
                          {r.conflicts.length ? (
                            <span className={styles.conflict}>
                              <StatusIcon level="bad" size={12} /> {r.conflicts.length} conflict{r.conflicts.length > 1 ? "s" : ""}
                            </span>
                          ) : null}
                          {r.warnings.length ? (
                            <span className={styles.chip} title={r.warnings.join("\n")}>
                              <StatusIcon level="warn" size={12} /> {r.warnings.length} note{r.warnings.length > 1 ? "s" : ""}
                            </span>
                          ) : null}
                        </span>
                      )}
                    </td>
                    <td className="num">
                      {nOps ? (
                        <span title={Object.entries(r.counts).map(([k, v]) => `${k}: ${v}`).join("\n")}>
                          {nOps}
                          {r.counts.trash ? <span className={styles.hint}> · {r.counts.trash} to trash</span> : null}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {!r.canonical ? (
                        <button type="button" className={styles.action} disabled={busy !== null} onClick={() => make("/api/organizer/plans", r.revisionId)}>
                          {busy === r.revisionId ? "Preparing…" : "Prepare plan"}
                        </button>
                      ) : null}{" "}
                      <button type="button" className={styles.action} disabled={busy !== null} onClick={() => make("/api/organizer/new-revision", r.revisionId)} title="Creates <Target> R<n+1> with an empty .SessionData and a target.json (after approval). Moves nothing.">
                        New revision…
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
