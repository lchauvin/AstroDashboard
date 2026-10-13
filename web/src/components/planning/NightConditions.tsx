import { Tip } from "@/components/ui";
import { CLEAR_CLOUD } from "@/lib/planning/forecast";
import type { NightGrid } from "@/lib/planning/night";
import type { RatedNight } from "@/server/planning";
import { localTime } from "./bits";
import { CloudChart, type ChartSlot } from "./CloudChart";
import styles from "./overview.module.css";

/** Astrospheric's transparency scale (~10 best … 29 worst), in words. */
const transparencyWord = (v: number) => (v <= 12 ? "Excellent" : v <= 15 ? "Good" : v <= 20 ? "Average" : v <= 24 ? "Poor" : "Bad");
/** Astrospheric's seeing (1–5, higher is better), in words. */
const seeingWord = (v: number) => ["Poor", "Poor", "Below average", "Average", "Good", "Excellent"][Math.max(0, Math.min(5, Math.round(v)))];

/** Moon-up spans inside the darkness, from the night grid's samples (each the centre of a step). */
function moonSpans(grid: NightGrid): { start: number; end: number }[] {
  const half = (grid.stepMinutes * 60_000) / 2;
  const spans: { start: number; end: number }[] = [];
  for (const s of grid.samples) {
    if (s.moonAlt <= 0) continue;
    const last = spans[spans.length - 1];
    if (last && s.t - half <= last.end + 1000) last.end = s.t + half;
    else spans.push({ start: s.t - half, end: s.t + half });
  }
  return spans;
}

function Cell({ label, value, sub, tip }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tip?: string }) {
  const body = (
    <div className={styles.cond}>
      <div className={styles.condLabel}>{label}</div>
      <div className={styles.condValue}>{value}</div>
      {sub ? <div className={styles.condSub}>{sub}</div> : null}
    </div>
  );
  return tip ? <Tip tip={tip} block>{body}</Tip> : body;
}

/** The night's forecast at a glance: six figures, then cloud through the night. */
export function NightConditions({ n, grid }: { n: RatedNight; grid: NightGrid }) {
  const f = n.forecast;
  const { night, quality: q } = n;
  const moon = moonSpans(grid);
  const moonText = !night.dusk
    ? "—"
    : !moon.length
      ? "Down all night"
      : moon.map((m) => `${m.start <= night.dusk! + 60_000 ? "up from dusk" : `rises ${localTime(m.start)}`}${m.end >= night.dawn! - 60_000 ? "" : `, sets ${localTime(m.end)}`}`).join("; ");

  if (!f || night.dusk == null || night.dawn == null) {
    return (
      <div className={styles.condGrid}>
        <Cell label="Darkness" value={night.dusk ? `${localTime(night.dusk)} – ${localTime(night.dawn)}` : "None"} sub={`${night.darkHours.toFixed(1)} h`} />
        <Cell label="Moon" value={`${Math.round(night.moonIllum * 100)} %`} sub={moonText} />
        <Cell label="Weather" value="Not forecast yet" sub="Astrospheric reaches about 10 days ahead" />
      </div>
    );
  }

  const temps = f.hours.map((h) => h.temperature?.value).filter((v): v is number => v != null);
  const dews = f.hours.map((h) => h.dewPoint?.value).filter((v): v is number => v != null);
  const winds = f.hours.map((h) => h.wind?.value).filter((v): v is number => v != null);
  const run = f.longestRun;
  const slots: ChartSlot[] = f.hours
    .filter((h) => h.cloud || h.gfsCloud || h.iconCloud)
    .map((h) => ({
      t: h.ms,
      step: h.step ?? 1,
      cloud: h.cloud?.value ?? null,
      gfs: h.gfsCloud?.value ?? null,
      icon: h.iconCloud?.value ?? null,
      transparency: h.transparency?.value ?? null,
      seeing: h.seeing?.value ?? null,
      extended: !!h.extended,
    }));

  return (
    <>
      <div className={styles.condGrid}>
        <Cell
          label="Clear sky"
          value={run ? `${localTime(run.start)} – ${localTime(run.end)}` : "No clear stretch"}
          sub={`${f.longestClear.toFixed(1)} h in a row · ${f.clearHours.toFixed(1)} h in all`}
          tip={`Clear = Astrospheric cloud below ${CLEAR_CLOUD} %. A night needs 5 h in a row to be worth setting up.`}
        />
        <Cell
          label="Cloud"
          value={f.meanCloud != null ? `${Math.round(f.meanCloud)} %` : "—"}
          sub={
            q.agreement != null
              ? `Models agree ${Math.round(q.agreement * 100)} %`
              : f.extended
                ? "Extended forecast (3-hourly)"
                : "GFS/ICON not available"
          }
          tip="Average cloud over the darkness. Model agreement: how often GFS and ICON make Astrospheric's clear/cloudy call, hour by hour."
        />
        <Cell
          label="Transparency"
          value={f.transparency != null ? `${transparencyWord(f.transparency)} · ${Math.round(f.transparency)}` : "—"}
          sub={f.smoke != null && f.smoke > 0 ? `Wildfire smoke (${f.smoke.toFixed(1)})` : f.transparency != null ? "Over the clear hours" : f.extended ? "Not in the extended forecast" : "Nothing clear to judge"}
          tip="Astrospheric's transparency including smoke: about 10 is excellent, 29 is the worst. Lower is better."
        />
        <Cell
          label="Seeing"
          value={f.seeing != null ? `${seeingWord(f.seeing)} · ${f.seeing.toFixed(1)}/5` : "—"}
          sub={f.seeing != null ? "At ~2.4″/px only poor seeing shows" : f.extended ? "Not in the extended forecast" : undefined}
          tip="Astrospheric's seeing forecast, 1–5, higher is better. Its coarsest forecast."
        />
        <Cell label="Moon" value={`${Math.round(night.moonIllum * 100)} %`} sub={moonText} />
        <Cell
          label="Dew · wind"
          value={f.dewRisk ? "Dew risk" : temps.length ? "Dry" : "—"}
          sub={
            temps.length
              ? `${Math.round(temps[0])}° → ${Math.round(Math.min(...temps))}°${dews.length ? `, dew pt ${Math.round(Math.min(...dews))}°` : ""}${winds.length ? ` · wind ≤ ${Math.max(...winds).toFixed(1)} m/s` : ""}`
              : f.extended
                ? "Not in the extended forecast"
                : undefined
          }
          tip="Dew risk: the temperature comes within 2° of the dew point. Wind at 10 m."
        />
      </div>
      <CloudChart dusk={night.dusk} dawn={night.dawn} slots={slots} moonUp={moon} clearRun={run} />
    </>
  );
}
