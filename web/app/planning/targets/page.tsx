import Link from "next/link";

import { EngineNotice } from "@/components/EngineNotice";
import { EfficiencyBars, GoalMini, MoonGlyph, SeasonStrip, localTime } from "@/components/planning/bits";
import { PlanControls } from "@/components/planning/PlanControls";
import styles from "@/components/planning/planning.module.css";
import { FilterChip, Marks } from "@/components/ui";
import { filterColor, hm, stageLabel } from "@/lib/format";
import { isoDate } from "@/lib/planning/night";
import type { TargetPlan } from "@/lib/planning/plan";
import type { NightPriority } from "@/lib/planning/priority";
import { FILTER_NAMES } from "@/lib/planning/rig";
import { bortleFromSqm } from "@/lib/planning/sites";
import { loadForecast, loadPlanningData, planFor, siteFor } from "@/server/planning";
import tableStyles from "@/components/TargetsTable.module.css";
import pageStyles from "../../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Planning · all targets" };

type Search = { site?: string; date?: string; finished?: string; sort?: string };

/** How the potential targets (Telescopius lists, new ideas) are ordered: the planner's score, or usable hours. */
type IdeaSort = "score" | "tonight" | "h90";

const IDEA_SORTS: Record<IdeaSort, (a: TargetPlan, b: TargetPlan) => number> = {
  score: (a, b) => b.score - a.score || b.hours90 - a.hours90,
  tonight: (a, b) => b.tonightHours - a.tonightHours || b.hours90 - a.hours90 || b.score - a.score,
  h90: (a, b) => b.hours90 - a.hours90 || b.tonightHours - a.tonightHours || b.score - a.score,
};

const detailHref = (p: TargetPlan, q: string) => `/planning/target/${encodeURIComponent(p.key)}${q}`;

export default async function PlanningTargetsPage({ searchParams }: { searchParams: Promise<Search> }) {
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
  const forecastSite = siteFor(data, sp.site);
  const forecast = forecastSite ? await loadForecast(forecastSite.id) : null;
  const result = planFor(data, { siteId: sp.site, date, includeFinished: sp.finished === "1", calendarDays: 1, forecast: forecast?.forecast });
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
  const sort: IdeaSort = sp.sort === "tonight" || sp.sort === "h90" ? sp.sort : "score";
  const ideas = [...result.ideas].sort(IDEA_SORTS[sort]);
  const sortHref = (s: IdeaSort) => `${q}${sp.finished === "1" ? "&finished=1" : ""}${s === "score" ? "" : `&sort=${s}`}`;
  const bortle = bortleFromSqm(site.sqm);
  const lSqm = calibration?.filters.L?.sqmEquivalent;
  const calibrated = calibration ? Object.values(calibration.filters) : [];

  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <Link href={`/planning?site=${site.id}`} className="eyebrow">← Planning · {site.name}</Link>
          <h1 className={pageStyles.title}>All targets</h1>
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
            {tonight.dusk ? `${hm(tonight.darkHours)} of astronomical darkness (sun below −18°)` : "The sun never reaches −18° tonight."}
          </div>
        </div>
        <div className={styles.tonightCell}>
          <div className="eyebrow">Moon</div>
          <div className={styles.big}>
            <MoonGlyph phase={tonight.moonPhase} size={24} />
            <span className="num">{Math.round(tonight.moonIllum * 100)} %</span>
          </div>
          <div className={styles.small}>{hm(tonight.moonFreeHours)} of darkness with the moon down</div>
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
          <h2 className="section-title">Filter priority</h2>
          <p className={styles.sectionNote}>
            {result.priority.summary} Your sky rules, when Astrospheric covers the night: OIII (or L/RGB) on clear, transparent
            nights with little moonlight; Hα unless the seeing is poor; SII otherwise. Percentages: what an hour tonight is worth
            against a moonless one, averaged over your active targets that are up. A target whose goal in a filter is met skips to
            the next one.
          </p>
        </div>
        <FilterPriorityList priority={result.priority} />
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">Your targets</h2>
          <p className={styles.sectionNote}>
            Score = usable hours × what an hour tonight is worth in its best filter × how far that filter is from its goal, with
            moonless hours favouring the filters the moon hurts most. Finished targets are {sp.finished === "1" ? "included" : "hidden"}.
          </p>
        </div>
        <YourTargets plans={result.yours} q={q} start={date} />
      </section>

      {data.lists.length || data.listsError ? (
        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2 className="section-title">From your Telescopius list{data.lists.length > 1 ? "s" : ""}</h2>
            <p className={styles.sectionNote}>
              {data.lists.map((l) => (
                <span key={l.id}>
                  {l.url ? (
                    <a href={l.url} target="_blank" rel="noreferrer">
                      {l.name}
                    </a>
                  ) : (
                    l.name
                  )}{" "}
                  ({l.targets.length}){" "}
                </span>
              ))}
              — ranked the same way{sort === "score" ? "" : ` (sorted by ${sort === "tonight" ? "tonight's" : "next 90 nights'"} hours)`}, out-of-season ones included. Targets already in your library are left out.
              {data.listsError ? ` Telescopius: ${data.listsError}` : ""}
            </p>
          </div>
          <Ideas plans={ideas.filter((p) => p.listed)} q={q} sort={sort} sortHref={sortHref} empty="Every listed target is already in your library." />
        </section>
      ) : null}

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">New ideas</h2>
          <p className={styles.sectionNote}>
            Bright nebulae and galaxies from OpenNGC and Sharpless that suit a {rig.fov.width.toFixed(1)}° field, not already
            in your library, {sort === "score" ? "ranked by tonight’s usable hours and framing" : `sorted by ${sort === "tonight" ? "tonight’s" : "the next 90 nights’"} usable hours`}.
            Click Tonight or Next 90 d to sort by hours; click again to go back to the ranking.
          </p>
        </div>
        <Ideas plans={ideas.filter((p) => !p.listed).slice(0, 25)} q={q} sort={sort} sortHref={sortHref} />
      </section>

      <p className={styles.foot}>
        Sky model calibrated from {calibration?.frames ?? 0} of your frames at {site.name}
        {calibrated.length
          ? `: ${calibrated.map((c) => `${FILTER_NAMES[c.letter]} κ ${c.kappa.toFixed(2)}${c.moonFrames ? ` moon ×${c.moonFactor.toFixed(1)}` : ""}`).join(", ")} (κ = measured sky ÷ the flat-spectrum SQM prediction; moon × = measured moonlight ÷ the Krisciunas–Schaefer model)`
          : " — none yet; analyse nights in Diagnostics to calibrate it"}
        . Hours per filter come from your goals (Match goals are measured from your subs). Computed in {result.computeMs} ms.
      </p>
    </main>
  );
}

function FilterPriorityList({ priority }: { priority: NightPriority }) {
  if (!priority.filters.length) return <p className="muted">No filters in the rig.</p>;
  return (
    <ol className={styles.priorityList}>
      {priority.filters.map((f, i) => (
        <li key={f.letter} className={styles.priorityItem}>
          <span className={styles.rank}>{i + 1}</span>
          <span className={styles.priorityName}>
            <FilterChip filter={f.letter} />
            {FILTER_NAMES[f.letter]}
          </span>
          <span className={styles.bar} style={{ width: 64, display: "block" }} aria-hidden>
            {f.efficiency != null ? <span style={{ width: `${Math.round(f.efficiency * 100)}%`, background: filterColor(f.letter) }} /> : null}
          </span>
          <span className="num" style={{ color: "var(--text-secondary)" }}>
            {f.efficiency != null ? `${Math.round(f.efficiency * 100)} %` : "—"}
          </span>
          <span className={styles.priorityReason}>
            {f.reason}
            {f.byRule ? <span className={styles.priorityRule}>RULE</span> : null}
          </span>
        </li>
      ))}
    </ol>
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
                {hm(p.tonightHours)}
                <div className={styles.sub}>max {Math.round(p.maxAlt)}°</div>
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

/** A column header that sorts the potential targets by it; clicking the active one returns to the ranking. */
function SortHead({ by, sort, sortHref, children }: { by: IdeaSort; sort: IdeaSort; sortHref: (s: IdeaSort) => string; children: React.ReactNode }) {
  const active = sort === by;
  return (
    <th aria-sort={active ? "descending" : undefined}>
      <Link href={sortHref(active ? "score" : by)} scroll={false} className={`${styles.sortHead} ${active ? styles.sortActive : ""}`} title={active ? "Back to the ranking" : "Sort by these hours, most first"}>
        {children}
        <span aria-hidden>{active ? " ▼" : " ↕"}</span>
      </Link>
    </th>
  );
}

function Ideas({
  plans,
  q,
  sort,
  sortHref,
  empty = "Nothing suitable is up at this site in the next 90 nights.",
}: {
  plans: TargetPlan[];
  q: string;
  sort: IdeaSort;
  sortHref: (s: IdeaSort) => string;
  empty?: string;
}) {
  if (!plans.length) return <p className="muted">{empty}</p>;
  return (
    <div className={tableStyles.scroller}>
      <table className="table">
        <thead>
          <tr>
            <th aria-label="Rank" />
            <th>Object</th>
            <th>Type · size</th>
            <SortHead by="tonight" sort={sort} sortHref={sortHref}>
              Tonight
            </SortHead>
            <SortHead by="h90" sort={sort} sortHref={sortHref}>
              Next 90 d
            </SortHead>
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
                </div>
              </td>
              <td className="num">{hm(p.tonightHours)}</td>
              <td className="num">{p.hours90.toFixed(0)} h</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
