import Link from "next/link";
import { notFound } from "next/navigation";

import { EngineNotice } from "@/components/EngineNotice";
import { PlanView } from "@/components/organizer/PlanView";
import { engineGet } from "@/lib/engine";
import type { OrgPlan } from "@/lib/organizer";
import styles from "../../library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Organizer plan" };

const STATUS: Record<string, string> = {
  draft: "Draft · waiting for your approval",
  blocked: "Blocked by conflicts",
  applying: "Applying…",
  applied: "Applied",
  partial: "Stopped part-way",
  failed: "Failed before any change",
  discarded: "Discarded",
};

export default async function PlanPage({ params }: { params: Promise<{ plan: string }> }) {
  const { plan: id } = await params;
  const res = await engineGet<OrgPlan>(`/api/organizer/plans/${encodeURIComponent(decodeURIComponent(id))}`);
  if (!res.ok) {
    if (res.reason === "not-found") notFound();
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const plan = res.data;
  return (
    <main className="page">
      <div className={styles.head}>
        <div>
          <div className="eyebrow">
            {STATUS[plan.status] ?? plan.status} · <span className="mono">{plan.id}</span>
          </div>
          <h1 className={styles.title}>{plan.title}</h1>
          {plan.undoOf ? <p className={styles.summary}>Reverses what plan {plan.undoOf} applied, newest step first.</p> : null}
          {plan.kind === "quarantine" ? (
            <p className={styles.summary}>
              Moves the night&apos;s rejected lights from .SessionData to the quarantine folder at the same relative path, so
              WBPP no longer finds them. &ldquo;Put back&rdquo; on the night&apos;s diagnostics reverses it.
            </p>
          ) : null}
          {plan.kind === "restore-quarantine" ? (
            <p className={styles.summary}>
              Puts back the lights the dashboard quarantined, in the folder each came from. Frames you set aside by hand stay
              where they are.
            </p>
          ) : null}
          {plan.kind && plan.revisionId && plan.session ? (
            <p className={styles.summary}>
              <Link href={`/diagnostics/${encodeURIComponent(plan.revisionId)}/${encodeURIComponent(plan.session)}?tab=frames`}>
                Back to {plan.session}
              </Link>
            </p>
          ) : null}
        </div>
      </div>
      <PlanView plan={plan} />
    </main>
  );
}
