import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { Checklist } from "@/components/Checklist";
import { EngineNotice } from "@/components/EngineNotice";
import { FilterIntegration } from "@/components/FilterIntegration";
import { SessionMatrix } from "@/components/SessionMatrix";
import { TelescopiusCard } from "@/components/TelescopiusCard";
import { Badge, Marks, StatTile } from "@/components/ui";
import { GoalsEditor } from "@/components/planning/GoalsEditor";
import { engineGet } from "@/lib/engine";
import { isoDate } from "@/lib/planning/night";
import { goalRowsFor, loadPlanningData, planOne } from "@/server/planning";
import { loadTelescopiusObject } from "@/server/telescopius";
import { hours, shortDate, stageLabel } from "@/lib/format";
import type { Revision } from "@/lib/types";
import styles from "./target.module.css";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ rev: string }> }) {
  const { rev } = await params;
  const res = await engineGet<Revision>(`/api/revisions/${encodeURIComponent(rev)}`);
  return { title: res.ok ? `${res.data.catalogId} ${res.data.name}` : "Target" };
}

export default async function TargetPage({ params }: { params: Promise<{ rev: string }> }) {
  const { rev: revId } = await params;
  const res = await engineGet<Revision>(`/api/revisions/${encodeURIComponent(revId)}`);
  if (!res.ok) {
    if (res.reason === "not-found") notFound();
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const rev = res.data;
  const t = rev.totals;
  const lights = rev.sessions.filter((s) => s.totals.lights > 0);
  // Usable = acquired minus what diagnostics rejected; shown once any night has been analysed.
  const analysed = t.analysedSeconds ?? 0;
  const accepted = t.acceptedSeconds ?? t.seconds;
  // Goals: the same model and editor as Planning, at the default site.
  const [planning, telescopius] = await Promise.all([loadPlanningData(), loadTelescopiusObject(rev.catalogId, rev.name)]);
  const one = planning.ok ? planOne(planning.data, rev.id, null, isoDate(new Date()), 0) : null;

  return (
    <main className="page">
      <Link href="/library" className={styles.back}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Library
      </Link>

      <div className={styles.head}>
        <div className={styles.headText}>
          <div className="eyebrow">
            {rev.folder}
          </div>
          <h1 className={styles.title}>
            {rev.catalogId}
            <span className={styles.titleName}>{rev.name}</span>
          </h1>
          <div className={styles.meta}>
            <Badge tone={rev.stage.value === "acquiring" ? "accent" : "neutral"}>{stageLabel(rev.stage.value)}</Badge>
            {rev.siblings.length > 1 ? (
              <span className={styles.revisions} role="group" aria-label="Revisions">
                {rev.siblings.map((s) => (
                  <Link
                    key={s.id}
                    href={`/library/${s.id}`}
                    className={s.id === rev.id ? `${styles.revLink} ${styles.revLinkActive}` : styles.revLink}
                    aria-current={s.id === rev.id ? "page" : undefined}
                  >
                    R{s.revision}
                  </Link>
                ))}
              </span>
            ) : (
              <Badge tone="muted">Revision {rev.revision}</Badge>
            )}
            {rev.panels.length ? <Badge tone="muted">{rev.panels.length}-panel mosaic</Badge> : null}
            {rev.equipment.map((e) => (
              <span key={`${e.instrume}${e.telescop}`} className={styles.equipment}>
                {e.telescop} · {e.focalLength} mm · {e.instrume}
              </span>
            ))}
          </div>
        </div>
        {rev.hasPreview ? (
          <div className={`framed ${styles.preview}`}>
            <Marks />
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/engine/api/revisions/${rev.id}/preview.jpg`} alt={`Latest finished image of ${rev.catalogId}`} />
          </div>
        ) : null}
      </div>

      <div className={analysed ? `${styles.stats} ${styles.stats5}` : styles.stats}>
        <StatTile label="Acquired" value={hours(t.seconds)} sub={`${t.lights} lights`} />
        {analysed ? (
          <StatTile
            label="Usable"
            value={hours(accepted)}
            sub={
              analysed < t.seconds
                ? `after diagnostics · ${hours(analysed)} of ${hours(t.seconds)} analysed`
                : `after diagnostics · −${hours(t.seconds - accepted)}`
            }
          />
        ) : null}
        <StatTile label="Nights" value={t.nights} sub={t.first ? `${shortDate(t.first)} → ${shortDate(t.last)}` : "—"} />
        <StatTile
          label="Filters"
          value={rev.filters.length}
          sub={rev.filters.map((f) => f.filter).join(" · ") || "—"}
        />
        <StatTile
          label="Set aside"
          value={t.rejected + t.quarantined}
          sub={`${t.rejected} rejected · ${t.quarantined} quarantined`}
        />
      </div>

      <div className={styles.columns}>
        <section className={styles.col}>
          <h2 className="section-title">Integration per filter</h2>
          <FilterIntegration filters={rev.filters} sessions={lights.length} />
        </section>
        <section className={styles.col}>
          <h2 className="section-title">Ready to process?</h2>
          <Checklist checks={rev.checks} />
        </section>
      </div>

      {telescopius?.keySet ? (
        <section className={styles.block}>
          <h2 className="section-title">The object</h2>
          <TelescopiusCard payload={telescopius} />
        </section>
      ) : null}

      {one && planning.ok ? (
        <section className={styles.block}>
          <div className={styles.blockHead}>
            <h2 className="section-title">Goals</h2>
            <Link href={`/planning/target/${encodeURIComponent(rev.id)}`} className="eyebrow">
              Plan this target →
            </Link>
          </div>
          <GoalsEditor
            revId={rev.id}
            rows={goalRowsFor(one)}
            capHours={planning.data.context.settings.goalCapHours}
            seasonHours={one.plan.hours90}
            siteName={one.site.name}
          />
        </section>
      ) : null}

      <section className={styles.block}>
        <div className={styles.blockHead}>
          <h2 className="section-title">Sessions</h2>
          <span className="eyebrow">Frames × exposure · flats · darks · framing · conditions</span>
        </div>
        <SessionMatrix rev={rev} />
      </section>

      {rev.excludedSessions.length ? (
        <section className={styles.block}>
          <h2 className="section-title">Excluded session folders</h2>
          <ul className={styles.plainList}>
            {rev.excludedSessions.map((e) => (
              <li key={e.folder}>
                <span className="mono">{e.folder}</span> — {e.reason} · <span className="num">{e.lights}</span> lights
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {rev.wbppMasters.length || rev.legacyStages.length || rev.finishedFiles ? (
        <section className={styles.block}>
          <h2 className="section-title">Processing</h2>
          <ul className={styles.plainList}>
            {rev.wbppMasters.map((w) => (
              <li key={w}>
                WBPP masters in <span className="mono">{w}</span>
              </li>
            ))}
            {rev.legacyStages.length ? (
              <li>
                Legacy manual-pipeline folders with files: <span className="mono">{rev.legacyStages.join(", ")}</span>
              </li>
            ) : null}
            {rev.finishedFiles ? (
              <li>
                <span className="num">{rev.finishedFiles}</span> files in <span className="mono">.Finished</span>
              </li>
            ) : null}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
