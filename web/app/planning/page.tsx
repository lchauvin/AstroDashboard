import Link from "next/link";

import { EngineNotice } from "@/components/EngineNotice";
import { BestPick, DewWarning, NightDetails, NightTile, QualityBadge, dayLabel, nightHref } from "@/components/planning/Nights";
import { PlanControls } from "@/components/planning/PlanControls";
import styles from "@/components/planning/overview.module.css";
import { Tip } from "@/components/ui";
import { isoDate } from "@/lib/planning/night";
import { isGood, nextGoodNight } from "@/lib/planning/quality";
import { loadPlanningData, ratedNights } from "@/server/planning";
import pageStyles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Planning" };

type Search = { site?: string };

/** How far ahead "next good night" looks (the forecast reaches ~10 days; beyond that, the moon decides). */
const LOOKAHEAD_DAYS = 30;

export default async function PlanningPage({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const res = await loadPlanningData();
  if (!res.ok) {
    return (
      <main className="page">
        <h1 className={pageStyles.title}>Planning</h1>
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const today = isoDate(new Date());
  const rated = await ratedNights(res.data, sp.site, today, LOOKAHEAD_DAYS);
  if (!rated) {
    return (
      <main className="page">
        <h1 className={pageStyles.title}>Planning</h1>
        <p className={pageStyles.summary}>
          No site yet. <Link href="/settings">Add one in Settings → Sites</Link>.
        </p>
      </main>
    );
  }
  const { result, payload, nights } = rated;
  const site = result.site;
  const tonight = nights[0];
  const next = nextGoodNight(nights);
  const week = nights.slice(1, 8);
  const tonightWorthIt = tonight.quality.level !== "poor";

  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">Planning · {site.name}</div>
          <h1 className={pageStyles.title}>Planning</h1>
        </div>
        <PlanControls sites={res.data.context.sites} siteId={site.id} date={today} showDate={false} />
      </div>

      <section className={styles.hero}>
        <div className={`${styles.card} ${styles.cardMain}`}>
          <div className={styles.cardDate}>Tonight · {dayLabel(today, { weekday: "long", month: "long", day: "numeric" })}</div>
          <Tip tip={<NightDetails q={tonight.quality} />}>
            <QualityBadge q={tonight.quality} large />
          </Tip>
          <div className={styles.summary}>
            {tonight.quality.summary}
            {tonight.quality.agreement != null ? ` · models agree ${Math.round(tonight.quality.agreement * 100)} %` : ""}
          </div>
          <DewWarning q={tonight.quality} long />
          <div className={styles.cardFoot}>
            {tonightWorthIt ? <BestPick n={tonight} siteId={site.id} /> : <span className={styles.muted}>Not worth setting up.</span>}
            <Link href={nightHref(today, site.id)}>Night details →</Link>
          </div>
        </div>

        <div className={styles.card}>
          <div className={styles.cardDate}>Next good night</div>
          {next ? (
            <>
              <div className={styles.cardTitle}>{dayLabel(next.night.date, { weekday: "long", month: "short", day: "numeric" })}</div>
              <Tip tip={<NightDetails q={next.quality} />}>
                <QualityBadge q={next.quality} />
              </Tip>
              <div className={styles.summary}>
                {next.basis === "forecast" ? next.quality.summary : `${next.quality.summary} — the best bet until the forecast reaches it.`}
                {next.quality.agreement != null ? ` · models agree ${Math.round(next.quality.agreement * 100)} %` : ""}
              </div>
              <DewWarning q={next.quality} long />
              <div className={styles.cardFoot}>
                <BestPick n={next} siteId={site.id} />
                <Link href={nightHref(next.night.date, site.id)}>Details →</Link>
              </div>
            </>
          ) : (
            <div className={styles.summary}>
              {isGood(tonight.quality) ? "Tonight is the one." : `Nothing in the next ${LOOKAHEAD_DAYS} nights.`}
            </div>
          )}
        </div>
      </section>

      <section>
        <h2 className="section-title">Next 7 nights</h2>
        <div className={styles.week} style={{ marginTop: 12 }}>
          {week.map((n) => (
            <NightTile key={n.night.date} n={n} siteId={site.id} />
          ))}
        </div>
      </section>

      <p className={styles.foot}>
        <Link href={`/planning/targets?site=${site.id}`}>All targets and new ideas →</Link>
        {payload?.error ? (
          <>
            <br />
            {payload.error}
          </>
        ) : null}
      </p>
    </main>
  );
}
