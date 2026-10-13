import Link from "next/link";
import { notFound } from "next/navigation";

import { EngineNotice } from "@/components/EngineNotice";
import { NightConditions } from "@/components/planning/NightConditions";
import { DewWarning, NightDetails, QualityBadge, dayLabel, nightHref } from "@/components/planning/Nights";
import { localTime } from "@/components/planning/bits";
import styles from "@/components/planning/overview.module.css";
import { FilterChip, Tip } from "@/components/ui";
import { isoDate } from "@/lib/planning/night";
import type { TargetPlan } from "@/lib/planning/plan";
import { FILTER_NAMES } from "@/lib/planning/rig";
import { loadPlanningData, ratedNights } from "@/server/planning";
import pageStyles from "../../../library/library.module.css";

export const dynamic = "force-dynamic";

type Params = { date: string };
type Search = { site?: string };

const RECOMMENDED = 5;
const IDEAS = 3;

export async function generateMetadata({ params }: { params: Promise<Params> }) {
  const { date } = await params;
  return { title: `Night of ${date}` };
}

const shift = (date: string, days: number) => isoDate(new Date(`${date}T12:00:00`), days);

export default async function NightPage({ params, searchParams }: { params: Promise<Params>; searchParams: Promise<Search> }) {
  const { date } = await params;
  const sp = await searchParams;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) notFound();
  const res = await loadPlanningData();
  if (!res.ok) {
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const rated = await ratedNights(res.data, sp.site, date, 1);
  if (!rated) notFound();
  const { result, nights } = rated;
  const site = result.site;
  const n = nights[0];
  const q = n.quality;
  const recs = result.yours.filter((p) => p.best && p.score > 0).slice(0, RECOMMENDED);
  const ideas = result.ideas.filter((p) => p.best).slice(0, IDEAS);
  const today = isoDate(new Date());

  return (
    <main className="page">
      <div className={styles.nav}>
        <Link href={`/planning?site=${site.id}`}>← Planning</Link>
        <span style={{ display: "flex", gap: 16 }}>
          {date > today ? (
            <Link href={nightHref(shift(date, -1), site.id)}>‹ {dayLabel(shift(date, -1), { weekday: "short" })}</Link>
          ) : null}
          <Link href={nightHref(shift(date, 1), site.id)}>{dayLabel(shift(date, 1), { weekday: "short" })} ›</Link>
        </span>
      </div>

      <div className="eyebrow">
        {date === today ? "Tonight · " : "Night of "}
        {dayLabel(date, {
          weekday: "long",
          month: "long",
          day: "numeric",
        })}{" "}
        · {site.name}
      </div>
      <h1 className={pageStyles.title} style={{ marginTop: 6 }}>
        <Tip tip={<NightDetails q={q} />}>
          <QualityBadge q={q} large />
        </Tip>
      </h1>
      <div className={styles.facts}>
        <span>{q.summary}</span>
        {n.night.dusk ? (
          <span>
            Dark {localTime(n.night.dusk)} – {localTime(n.night.dawn)}
          </span>
        ) : null}
      </div>

      {q.dewFrom != null && q.level !== "poor" ? (
        <div style={{ margin: "-18px 0 26px" }}>
          <DewWarning q={q} long />
        </div>
      ) : null}

      <section style={{ marginBottom: 32 }}>
        <h2 className="section-title">Conditions</h2>
        <NightConditions n={n} grid={result.tonight} />
      </section>

      {q.level === "poor" ? null : (
        <section>
          <h2 className="section-title">Recommended</h2>
          {recs.length ? (
            <ol className={styles.recs} style={{ marginTop: 10 }}>
              {recs.map((p, i) => (
                <Rec key={p.key} p={p} rank={i + 1} siteId={site.id} date={date} />
              ))}
            </ol>
          ) : (
            <p className="muted">None of your targets is up in the dark this night, or every goal is met.</p>
          )}
          {ideas.length ? (
            <p className={styles.more}>
              New ideas:{" "}
              {ideas.map((p, i) => (
                <span key={p.key}>
                  {i ? ", " : ""}
                  <Tip tip={`${p.classLabel} · ${p.tonightHours.toFixed(1)} h up · ${p.advice}`}>
                    <Link href={`/planning/target/${encodeURIComponent(p.key)}?site=${site.id}&date=${date}`}>{p.title}</Link>
                  </Tip>
                </span>
              ))}
            </p>
          ) : null}
          <p className={styles.more}>
            <Link href={`/planning/targets?site=${site.id}&date=${date}`}>All targets and ideas for this night →</Link>
          </p>
        </section>
      )}
    </main>
  );
}

function Rec({ p, rank, siteId, date }: { p: TargetPlan; rank: number; siteId: string; date: string }) {
  return (
    <li className={styles.rec}>
      <span className={styles.recRank}>{rank}</span>
      <div>
        <Link href={`/planning/target/${encodeURIComponent(p.key)}?site=${siteId}&date=${date}`} className={styles.recTitle}>
          {p.title}
        </Link>
        <div className={styles.recSub}>
          {p.tonightHours.toFixed(1)} h up in the dark · max {Math.round(p.maxAlt)}°
        </div>
      </div>
      <Tip tip={p.advice}>
        <span className={styles.recFilter}>
          <FilterChip filter={p.best!.letter} />
          {FILTER_NAMES[p.best!.letter]}
        </span>
      </Tip>
    </li>
  );
}
