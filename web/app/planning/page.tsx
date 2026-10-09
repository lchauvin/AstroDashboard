import Link from "next/link";

import { EngineNotice } from "@/components/EngineNotice";
import { EfficiencyBars, GoalMini, MoonGlyph, SeasonStrip, localTime } from "@/components/planning/bits";
import { PlanControls } from "@/components/planning/PlanControls";
import styles from "@/components/planning/planning.module.css";
import { FilterChip, Marks, Tip } from "@/components/ui";
import { stageLabel } from "@/lib/format";
import { isoDate } from "@/lib/planning/night";
import type { TargetPlan } from "@/lib/planning/plan";
import { FILTER_NAMES } from "@/lib/planning/rig";
import { bortleFromSqm } from "@/lib/planning/sites";
import { loadPlanningData, planFor } from "@/server/planning";
import tableStyles from "@/components/TargetsTable.module.css";
import pageStyles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Planning" };

type Search = { site?: string; date?: string; finished?: string };

const detailHref = (p: TargetPlan, q: string) => `/planning/target/${encodeURIComponent(p.key)}${q}`;

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
  const data = res.data;
  const date = sp.date && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date : isoDate(new Date());
  const result = planFor(data, { siteId: sp.site, date, includeFinished: sp.finished === "1" });
  if (!result) {
    return (
      <main className="page">
        <h1 className={pageStyles.title}>Planning</h1>
        <p className={pageStyles.summary}>
          No site yet. <Link href="/settings">Add one in Settings → Sites</Link>.
        </p>
      </main>
    );
  }
  const { site, tonight, rig, calibration } = result;
  const q = `?site=${site.id}&date=${date}`;
  const bortle = bortleFromSqm(site.sqm);
  const lSqm = calibration?.filters.L?.sqmEquivalent;
  const calibrated = calibration ? Object.values(calibration.filters) : [];

  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">What to shoot next · {site.name}</div>
          <h1 className={pageStyles.title}>Planning</h1>
          <p className={pageStyles.summary}>
            Usable hours in astronomical darkness above {site.name}&apos;s horizon, the moon&apos;s cost per filter, and
            your goals — ranked for the night of {new Date(`${date}T12:00:00`).toLocaleDateString("en-CA", { weekday: "long", month: "long", day: "numeric" })}.
          </p>
        </div>
        <PlanControls sites={data.context.sites} siteId={site.id} date={date} showFinished={sp.finished === "1"} />
      </div>

      <section className={`framed ${styles.tonight}`} aria-label="Tonight">
        <Marks />
        <div className={styles.tonightCell}>
          <div className="eyebrow">Darkness</div>
          <div className={`${styles.big} num`}>
            {tonight.dusk ? `${localTime(tonight.dusk)} – ${localTime(tonight.dawn)}` : "None"}
          </div>
          <div className={styles.small}>
            {tonight.dusk ? `${tonight.darkHours.toFixed(1)} h of astronomical darkness (sun below −18°)` : "The sun never reaches −18° tonight."}
          </div>
        </div>
        <div className={styles.tonightCell}>
          <div className="eyebrow">Moon</div>
          <div className={styles.big}>
            <MoonGlyph phase={tonight.moonPhase} size={24} />
            <span className="num">{Math.round(tonight.moonIllum * 100)} %</span>
          </div>
          <div className={styles.small}>{tonight.moonFreeHours.toFixed(1)} h of darkness with the moon down</div>
        </div>
        <div className={styles.tonightCell}>
          <div className="eyebrow">Sky at {site.name}</div>
          <div className={`${styles.big} num`}>SQM {site.sqm.toFixed(1)}</div>
          <div className={styles.small}>
            Bortle {bortle.class} · floor {site.minAlt}°{site.horizon.length ? " + horizon profile" : ""}
            {lSqm != null ? (
              <>
                <br />
                Your L frames measure ≈ SQM {lSqm.toFixed(2)}
              </>
            ) : null}
          </div>
        </div>
        <div className={styles.tonightCell}>
          <div className="eyebrow">Rig</div>
          <div className={styles.big} style={{ fontSize: 18 }}>
            {rig.telescope.name.replace("William Optics ", "")}
          </div>
          <div className={styles.small}>
            {rig.camera.name} · {rig.pixelScale.toFixed(2)}″/px · {rig.fov.width.toFixed(1)}° × {rig.fov.height.toFixed(1)}°
            <br />
            <Link href="/settings?tab=rig">Settings → Rig</Link>
          </div>
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">Your targets</h2>
          <p className={styles.sectionNote}>
            Score = usable hours × the filter&apos;s efficiency tonight × how far it is from its goal, with moonless hours
            favouring the filters the moon hurts most. Finished targets are {sp.finished === "1" ? "included" : "hidden"}.
          </p>
        </div>
        <YourTargets plans={result.yours} q={q} start={date} />
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">Next {result.calendar.length} nights</h2>
          <p className={styles.sectionNote}>Bar: share of the night&apos;s darkness with the moon down. Best of your targets and its filter for each night.</p>
        </div>
        <div className={styles.calendar}>
          {result.calendar.map((n, i) => (
            <div key={n.date} className={i === 0 ? `${styles.night} ${styles.nightToday}` : styles.night}>
              <div className={styles.nightDate}>
                <span>{new Date(`${n.date}T12:00:00`).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" })}</span>
                <Tip tip={`Moon ${Math.round(n.moonIllum * 100)} % lit`}>
                  <MoonGlyph phase={n.moonPhase} size={16} />
                </Tip>
              </div>
              <Tip tip={`${n.moonFreeHours.toFixed(1)} of ${n.darkHours.toFixed(1)} h dark with the moon down`} block>
                <div className={styles.nightBar}>
                  <span style={{ width: `${n.darkHours ? Math.round((100 * n.moonFreeHours) / n.darkHours) : 0}%` }} />
                </div>
              </Tip>
              <div className={styles.nightMeta}>
                {n.darkHours.toFixed(1)} h dark · moon {Math.round(n.moonIllum * 100)} %
              </div>
              {n.best ? (
                <Link href={`/planning/target/${encodeURIComponent(n.best.key)}?site=${site.id}&date=${n.date}`} className={styles.nightBest}>
                  <FilterChip filter={n.best.letter} />
                  <span>{n.best.title}</span>
                </Link>
              ) : (
                <span className={styles.nightMeta}>—</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">New ideas</h2>
          <p className={styles.sectionNote}>
            Bright nebulae and galaxies from OpenNGC and Sharpless that suit a {rig.fov.width.toFixed(1)}° field, not already
            in your library, ranked by tonight&apos;s usable hours, brightness (hours to SNR 30) and framing.
            {data.halphaPending ? ` ${data.halphaPending} Hα survey values are still being fetched in the background.` : ""}
          </p>
        </div>
        <Ideas plans={result.ideas.slice(0, 25)} q={q} />
      </section>

      <p className={styles.foot}>
        Sky model calibrated from {calibration?.frames ?? 0} of your frames at {site.name}
        {calibrated.length
          ? `: ${calibrated.map((c) => `${FILTER_NAMES[c.letter]} κ ${c.kappa.toFixed(2)}`).join(", ")} (κ = measured sky ÷ the flat-spectrum SQM prediction)`
          : " — none yet; analyse nights in Diagnostics to calibrate it"}
        . Target brightness is estimated (Hα survey or class typical, OIII/SII by class line ratios). Computed in {result.computeMs} ms.
      </p>
    </main>
  );
}

function YourTargets({ plans, q, start }: { plans: TargetPlan[]; q: string; start: string }) {
  if (!plans.length) return <p className="muted">No targets with a known position.</p>;
  return (
    <div className={tableStyles.scroller}>
      <table className="table">
        <thead>
          <tr>
            <th aria-label="Rank" />
            <th>Target</th>
            <th>Tonight</th>
            <th>Suggestion</th>
            <th>Filters tonight</th>
            <th>Goals</th>
            <th>Next 30 / 90 d</th>
            <th>Season</th>
          </tr>
        </thead>
        <tbody>
          {plans.map((p, i) => (
            <tr key={p.key}>
              <td className={styles.rank}>{p.score > 0 ? i + 1 : ""}</td>
              <td className={styles.targetCell}>
                <Link href={detailHref(p, q)} className={styles.targetLink}>
                  {p.title}
                </Link>
                <div className={styles.sub}>
                  {p.subtitle} · {stageLabel(p.target?.stage ?? "")}
                </div>
              </td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>
                {p.tonightHours.toFixed(1)} h
                <div className={styles.sub}>max {Math.round(p.maxAlt)}°</div>
              </td>
              <td className={styles.adviceCell}>
                {p.best ? (
                  <div className={styles.best}>
                    <FilterChip filter={p.best.letter} />
                    {FILTER_NAMES[p.best.letter]}
                  </div>
                ) : null}
                <div className={styles.advice}>{p.advice}</div>
              </td>
              <td>
                <EfficiencyBars nights={p.tonight} relevant={p.relevant} />
              </td>
              <td>
                <GoalMini goals={p.goals} />
              </td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>
                {p.hours30.toFixed(0)} / {p.hours90.toFixed(0)} h
              </td>
              <td>{p.season ? <SeasonStrip hours={p.season} start={start} /> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Ideas({ plans, q }: { plans: TargetPlan[]; q: string }) {
  if (!plans.length) return <p className="muted">Nothing suitable is up at this site in the next 90 nights.</p>;
  return (
    <div className={tableStyles.scroller}>
      <table className="table">
        <thead>
          <tr>
            <th aria-label="Rank" />
            <th>Object</th>
            <th>Type · size</th>
            <th>Tonight</th>
            <th>Next 90 d</th>
            <th>To SNR 30</th>
            <th>Suggestion</th>
          </tr>
        </thead>
        <tbody>
          {plans.map((p, i) => (
            <tr key={p.key}>
              <td className={styles.rank}>{i + 1}</td>
              <td className={styles.targetCell}>
                <Link href={detailHref(p, q)} className={styles.targetLink}>
                  {p.title}
                </Link>
                {p.subtitle ? <div className={styles.sub}>{p.subtitle}</div> : null}
              </td>
              <td>
                {p.classLabel}
                <div className={styles.sub}>
                  {p.info?.majAxArcmin ? `${Math.round(p.info.majAxArcmin)}′${p.info.minAxArcmin ? ` × ${Math.round(p.info.minAxArcmin)}′` : ""}` : "size unknown"}
                  {p.anchor.basis === "survey" ? ` · ${Math.round(p.anchor.rayleighs)} R measured` : ""}
                </div>
              </td>
              <td className="num">{p.tonightHours.toFixed(1)} h</td>
              <td className="num">{p.hours90.toFixed(0)} h</td>
              <td className="num">{p.hoursToSnr30 == null ? "—" : `${p.hoursToSnr30 < 10 ? p.hoursToSnr30.toFixed(1) : Math.round(p.hoursToSnr30)} h`}</td>
              <td className={styles.adviceCell}>
                {p.best ? (
                  <div className={styles.best}>
                    <FilterChip filter={p.best.letter} />
                    {FILTER_NAMES[p.best.letter]}
                  </div>
                ) : null}
                <div className={styles.advice}>{p.advice}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
