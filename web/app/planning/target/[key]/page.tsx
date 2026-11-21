import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { EngineNotice } from "@/components/EngineNotice";
import { AltitudeChart } from "@/components/planning/AltitudeChart";
import { SeasonStrip } from "@/components/planning/bits";
import { GoalsEditor } from "@/components/planning/GoalsEditor";
import { PlanControls } from "@/components/planning/PlanControls";
import { TelescopiusCard } from "@/components/TelescopiusCard";
import styles from "@/components/planning/planning.module.css";
import { Badge, FilterChip, Marks } from "@/components/ui";
import { hm, stageLabel } from "@/lib/format";
import { formatDec, formatRa } from "@/lib/astro/coordinates";
import { isoDate } from "@/lib/planning/night";
import { FILTER_NAMES } from "@/lib/planning/rig";
import { nightSeries } from "@/lib/planning/series";
import { optimalSubSeconds, readNoisePenalty } from "@/lib/snr";
import { goalRowsFor, loadForecast, loadPlanningData, matchMeasuring, planOne, siteFor } from "@/server/planning";
import { loadTelescopiusObject } from "@/server/telescopius";
import tableStyles from "@/components/TargetsTable.module.css";
import pageStyles from "../../../library/library.module.css";

export const dynamic = "force-dynamic";

type Params = { key: string };
type Search = { site?: string; date?: string };

export async function generateMetadata({ params }: { params: Promise<Params> }) {
  const { key } = await params;
  const k = decodeURIComponent(key);
  return { title: k.startsWith("idea:") ? k.slice(5) : "Target plan" };
}

const rate = (v: number) => (v >= 1 ? v.toFixed(2) : v >= 0.01 ? v.toFixed(3) : v.toExponential(1));

export default async function TargetPlanPage({ params, searchParams }: { params: Promise<Params>; searchParams: Promise<Search> }) {
  const { key: rawKey } = await params;
  const sp = await searchParams;
  const key = decodeURIComponent(rawKey);
  const res = await loadPlanningData();
  if (!res.ok) {
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const data = res.data;
  const date = sp.date && /^\d{4}-\d{2}-\d{2}$/.test(sp.date) ? sp.date : isoDate(new Date());
  const forecastSite = siteFor(data, sp.site);
  const forecast = forecastSite ? await loadForecast(forecastSite.id) : null;
  const one = planOne(data, key, sp.site, date, 365, forecast?.forecast);
  if (!one) notFound();
  const { site, result, plan: p, catalog } = one;
  const telescopius = await loadTelescopiusObject(p.target?.catalogId ?? catalog?.name, p.target?.name ?? catalog?.commonName);
  const { rig, skies, tonight } = result;
  const cal = result.calibration;
  const series = nightSeries(site, tonight, p.ra, p.dec);
  const q = `?site=${site.id}&date=${date}`;

  const shownLetters = p.relevant.filter((l) => rig.filters[l]);

  const goalRows = goalRowsFor(data, one);

  // Compare sites: same target, same night, every site.
  const compare = data.context.sites.map((s) => {
    const o = s.id === site.id ? one : planOne(data, key, s.id, date, 0);
    const remaining = o?.plan.goals.filter((g) => g.goal && g.goal.mode !== "none" && g.remainingHours != null) ?? [];
    return { site: s, plan: o?.plan ?? null, remaining };
  });

  return (
    <main className="page">
      <Link href={`/planning${q}`} className={pageStyles.scanned} style={{ display: "inline-flex", gap: 4, alignItems: "center", marginBottom: 14 }}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Planning
      </Link>
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">
            {p.classLabel}
            {catalog?.majAxArcmin ? ` · ${Math.round(catalog.majAxArcmin)}′${catalog.minAxArcmin ? ` × ${Math.round(catalog.minAxArcmin)}′` : ""}` : ""} · RA {formatRa(p.ra)} · Dec {formatDec(p.dec)}
          </div>
          <h1 className={pageStyles.title}>{p.title}</h1>
          <p className={pageStyles.summary}>
            {p.subtitle}
            {p.target ? (
              <>
                {" "}
                · <Badge tone={p.target.stage === "acquiring" ? "accent" : "neutral"}>{stageLabel(p.target.stage)}</Badge>{" "}
                <Link href={`/library/${p.target.id}`}>Open in Library</Link>
              </>
            ) : (
              " · not in your library yet"
            )}
          </p>
        </div>
        <PlanControls sites={data.context.sites} siteId={site.id} date={date} />
      </div>

      {telescopius?.keySet ? (
        <section className={styles.section}>
          <TelescopiusCard payload={telescopius} />
        </section>
      ) : null}

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">The night of {new Date(`${date}T12:00:00`).toLocaleDateString("en-CA", { month: "long", day: "numeric" })}</h2>
          <p className={styles.sectionNote}>
            {p.tonightHours.toFixed(1)} usable hours at {site.name} (dark, above {site.minAlt}°{site.horizon.length ? " and the horizon profile" : ""}). {p.advice}
          </p>
        </div>
        <div className="framed" style={{ padding: "18px 20px" }}>
          <Marks />
          <AltitudeChart points={series} title={p.title} />
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">Per filter</h2>
          <p className={styles.sectionNote}>
            Sky in e⁻/s/px for your rig ({rig.pixelScale.toFixed(2)}″/px): &quot;measured&quot; where your own analysed subs at{" "}
            {site.name} calibrated it (κ), otherwise the flat-spectrum SQM estimate. Tonight vs moonless: what an hour tonight is
            worth against a moonless hour, from how much the moon brightens this filter&apos;s sky.
          </p>
        </div>
        <div className={tableStyles.scroller}>
          <table className="table">
            <thead>
              <tr>
                <th>Filter</th>
                <th className="num">Dark sky</th>
                <th className="num">Tonight vs moonless</th>
                <th className="num">Sub · read-noise cost</th>
                <th className="num">Optimal sub</th>
              </tr>
            </thead>
            <tbody>
              {shownLetters.map((l) => {
                const fs = skies[l];
                const night = p.tonight.find((n) => n.letter === l);
                const c = cal?.filters[l];
                const sub = rig.subSeconds[l] ?? 300;
                // Sky-limited: the faint target adds next to nothing to the noise, so only the sky and the camera count.
                const params = { objectRate: 0, skyRate: fs?.darkRate ?? 0, darkRate: rig.camera.darkCurrentEPerSec, readNoise: rig.camera.readNoiseE };
                const penalty = readNoisePenalty({ ...params, subSeconds: sub });
                const opt = optimalSubSeconds(params);
                return (
                  <tr key={l}>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <FilterChip filter={l} /> {FILTER_NAMES[l]}
                      {rig.filters[l]?.line ? <div className={styles.sub}>{rig.filters[l]!.fwhmNm} nm</div> : null}
                    </td>
                    <td className="num">
                      {fs ? rate(fs.darkRate) : "—"}
                      <div className={styles.sub} style={{ whiteSpace: "nowrap" }} title={c ? `Calibrated from ${c.frames} of your frames (${c.moonDownFrames} with the moon down)${c.moonFrames ? `; moonlight ×${c.moonFactor.toFixed(2)} the Krisciunas–Schaefer model, from ${c.moonFrames} moonlit frames on ${c.moonNights} nights` : "; moonlight from the Krisciunas–Schaefer model (too few moonlit nights to measure)"}` : undefined}>
                        {c ? `measured, κ ${c.kappa.toFixed(2)}${c.moonFrames ? `, moon ×${c.moonFactor.toFixed(1)}` : ""}` : "estimated"}
                      </div>
                    </td>
                    <td className="num">{night && night.hours > 0 ? `${Math.round(night.efficiency * 100)} %` : "—"}</td>
                    <td className="num">
                      {sub} s · {Number.isFinite(penalty) ? ((c2) => (c2 < 0.5 ? "< 1 %" : `−${Math.round(c2)} %`))((1 - 1 / (penalty * penalty)) * 100) : "—"}
                    </td>
                    <td className="num">{Number.isFinite(opt) ? (opt > 1800 ? "> 30 min" : `${Math.round(opt)} s`) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {p.target ? (
        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2 className="section-title">Goals</h2>
            <p className={styles.sectionNote}>
              Per filter: hours, Match (as good as another target in that filter, measured from both targets&apos; subs), or none.
              Good hours count only frames kept by Diagnostics where a night was analysed.
              {p.target.goalsSource === "target.json" ? " Current goals come from the revision's target.json; saving stores them in the dashboard." : ""}
            </p>
          </div>
          <GoalsEditor revId={p.target.id} rows={goalRows} capHours={data.context.settings.goalCapHours} seasonHours={p.hours90} measuring={matchMeasuring(data, p.target)} />
        </section>
      ) : null}

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className="section-title">Compare sites</h2>
          <p className={styles.sectionNote}>Same target and night at every site: is the drive worth it?</p>
        </div>
        <div className={tableStyles.scroller}>
          <table className="table">
            <thead>
              <tr>
                <th>Site</th>
                <th className="num">SQM</th>
                <th className="num">Tonight</th>
                <th>Best filter tonight</th>
                <th className="num">Next 30 d</th>
                <th>Hours still needed (goals)</th>
              </tr>
            </thead>
            <tbody>
              {compare.map(({ site: s, plan: cp, remaining }) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/planning/target/${encodeURIComponent(key)}?site=${s.id}&date=${date}`} className={styles.targetLink}>
                      {s.name}
                    </Link>
                    {s.id === site.id ? <span className={styles.sub}> · selected</span> : null}
                  </td>
                  <td className="num">{s.sqm.toFixed(1)}</td>
                  <td className="num">{cp ? hm(cp.tonightHours) : "—"}</td>
                  <td>{cp?.best ? <><FilterChip filter={cp.best.letter} /> {Math.round(cp.best.efficiency * 100)} %</> : "—"}</td>
                  <td className="num">{cp ? `${cp.hours30.toFixed(0)} h` : "—"}</td>
                  <td>
                    {remaining.length
                      ? remaining.map((g) => `${g.letter} ${hm(g.remainingHours!)}`).join(" · ")
                      : <span className="muted">no goals set</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.context.sites.length < 2 ? (
          <p className={styles.foot}>
            Only one site so far. <Link href="/settings">Add a dark site in Settings → Sites</Link> to compare.
          </p>
        ) : null}
      </section>

      {p.season ? (
        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2 className="section-title">Season</h2>
            <p className={styles.sectionNote}>Usable hours over the next year at {site.name}.</p>
          </div>
          <div className="framed" style={{ padding: "18px 20px" }}>
            <Marks />
            <SeasonStrip hours={p.season} start={date} />
          </div>
        </section>
      ) : null}
    </main>
  );
}
