"use client";

import { useEffect, useState } from "react";

import { filterLetterOf, type AcState, type AcStep, type LivePayload } from "@/lib/live";
import { FilterChip, Marks, StatusIcon, Tip } from "../ui";
import styles from "./live.module.css";

const POLL_MS = 3000;

/** Deepest running step, with its parents for context ("Targets › M 31 › Take Exposures"). */
function runningPath(steps: AcStep[] = []): string[] {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const running = steps.filter((s) => s.status === "RUNNING");
  const leaf = running.sort((a, b) => b.depth - a.depth)[0];
  const out: string[] = [];
  for (let s: AcStep | undefined = leaf; s; s = s.parent_id ? byId.get(s.parent_id) : undefined) out.unshift(s.name);
  return out;
}

const fmt = (v: number | null | undefined, d = 1, unit = "") => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(d)}${unit}`);

export function LiveView({ initial }: { initial: LivePayload }) {
  const [live, setLive] = useState<LivePayload>(initial);
  const [updated, setUpdated] = useState<number>(Date.now());

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const res = await fetch("/engine/api/live/state", { cache: "no-store" });
        if (res.ok && !stop) {
          setLive(await res.json());
          setUpdated(Date.now());
        }
      } catch {}
    };
    const id = setInterval(tick, POLL_MS);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, []);

  if (!live.online || !live.state) {
    return (
      <div className={`framed ${styles.offline}`}>
        <Marks />
        <StatusIcon level="bad" size={16} />
        <div>
          <strong>AstroController offline at {live.url}</strong>
          <p className={styles.hint}>
            {live.error ?? "No answer."} Start it with <span className="mono">uv run astrocontroller</span> in D:\Python\AstroController,
            or change its address in Settings → Live · Ingest. This page retries every few seconds.
            {!live.tokenSet ? " If it runs with a token, set ASTROCONTROLLER_TOKEN for the engine." : ""}
          </p>
        </div>
      </div>
    );
  }

  const s: AcState = live.state;
  const eq = s.nina?.equipment ?? {};
  const path = runningPath(s.sequence?.steps);
  const frames = (s.frames ?? []).filter((f) => f.image_type === "LIGHT");
  const latest = frames[0];
  const g = s.guiding;
  const w = s.weather?.current;

  return (
    <div className={styles.live}>
      <div className={styles.statusLine}>
        <StatusIcon level="ok" size={14} label="Connected" /> Connected to <a href={live.url} target="_blank" rel="noreferrer">AstroController</a> · updated{" "}
        {Math.max(0, Math.round((Date.now() - updated) / 1000))} s ago · read-only (controls stay in AstroController)
      </div>

      <div className={styles.grid}>
        <section className={`framed ${styles.card} ${styles.now}`}>
          <Marks />
          <div className="eyebrow">Now</div>
          <div className={styles.big}>{latest?.target ?? "—"}</div>
          <div className={styles.path}>{path.length ? path.join(" › ") : s.sequence?.available ? "Sequence idle" : "No sequence"}</div>
          <dl className={styles.facts}>
            <dt>Filter</dt>
            <dd>{eq.filterwheel?.SelectedFilter?.Name ? <><FilterChip filter={filterLetterOf(eq.filterwheel.SelectedFilter.Name)} /> <span className={styles.hint}>{eq.filterwheel.SelectedFilter.Name}</span></> : "—"}</dd>
            <dt>Camera</dt>
            <dd>
              {eq.camera?.IsExposing ? "exposing" : "idle"} · {fmt(eq.camera?.Temperature, 1, " °C")} (set {fmt(eq.camera?.TemperatureSetPoint, 0, " °C")}, cooler {fmt(eq.camera?.CoolerPower, 0, " %")})
            </dd>
            <dt>Mount</dt>
            <dd>
              alt {fmt(eq.mount?.Altitude, 0, "°")} · az {fmt(eq.mount?.Azimuth, 0, "°")} · {eq.mount?.SideOfPier?.replace("pier", "") ?? "?"}
              {eq.mount?.TimeToMeridianFlip != null ? ` · flip in ${fmt(eq.mount.TimeToMeridianFlip, 1, " h")}` : ""}
            </dd>
            <dt>Guiding</dt>
            <dd>
              {s.phd2?.app_state ?? "—"}
              {s.phd2?.settling ? " (settling)" : ""}
            </dd>
            <dt>Focuser</dt>
            <dd>
              {eq.focuser?.Position ?? "—"} · {fmt(eq.focuser?.Temperature, 1, " °C")}
            </dd>
          </dl>
        </section>

        <section className={`framed ${styles.card}`}>
          <Marks />
          <div className="eyebrow">Latest frame{s.preview?.filename ? ` · ${s.preview.filename}` : ""}</div>
          {s.preview?.available ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img className={styles.frame} src={`/engine/api/live/frame.png?v=${s.preview.token ?? ""}`} alt="Latest frame, stretched" />
          ) : (
            <p className={styles.hint}>No preview yet.</p>
          )}
        </section>

        <section className={`framed ${styles.card}`}>
          <Marks />
          <div className="eyebrow">Guiding · arcsec</div>
          <div className={styles.rms}>
            <span>
              RA <b className="num">{fmt(g?.rms?.rms_ra, 2)}</b>
            </span>
            <span>
              Dec <b className="num">{fmt(g?.rms?.rms_dec, 2)}</b>
            </span>
            <span>
              Total <b className="num">{fmt(g?.rms?.rms_total, 2)}</b>
            </span>
          </div>
          {g?.graph?.t?.length ? <GuideChart graph={g.graph} /> : <p className={styles.hint}>No guide steps yet.</p>}
          <div className={styles.guideRow}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className={styles.guideStar} src={`/engine/api/live/guide-star.png?v=${s.guide_image?.token ?? ""}`} alt="Guide star" />
            <span className={styles.hint}>
              Advisor: {s.advisor?.enabled ? s.advisor.mode : "off"}
              {s.advisor?.last?.summary ? ` — ${s.advisor.last.summary}` : ""}
            </span>
          </div>
        </section>

        <section className={`framed ${styles.card}`}>
          <Marks />
          <div className="eyebrow">Conditions</div>
          <dl className={styles.facts}>
            <dt>Clouds</dt>
            <dd>{fmt(w?.cloud_total, 0, " %")} {s.weather?.summary?.clear_hours != null ? `· ${s.weather.summary.clear_hours} clear h ahead` : ""}</dd>
            <dt>Air</dt>
            <dd>
              {fmt(w?.temp_c, 1, " °C")} · RH {fmt(w?.humidity, 0, " %")} · wind {fmt(w?.wind_ms, 1, " m/s")}
            </dd>
            <dt>Dew margin</dt>
            <dd className={(w?.dewpoint_spread ?? 99) < 3 ? styles.warnText : undefined}>
              {fmt(w?.dewpoint_spread, 1, " °C")}
              {s.weather?.summary?.dew_risk ? " · dew risk" : ""}
            </dd>
            <dt>Moon</dt>
            <dd>
              {s.sky?.illumination != null ? `${Math.round(s.sky.illumination * 100)} % lit` : "—"} · {s.sky?.up ? `up ${fmt(s.sky?.altitude_deg, 0, "°")}` : "down"}
              {s.sky?.separation_deg != null ? ` · ${fmt(s.sky.separation_deg, 0, "°")} from target` : ""}
            </dd>
          </dl>
        </section>
      </div>

      <section className={styles.block}>
        <h2 className="section-title">Tonight&apos;s frames</h2>
        {frames.length ? (
          <div className={styles.scroller}>
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Time</th>
                  <th>Target</th>
                  <th>Filter</th>
                  <th className="num">Exp</th>
                  <th className="num">HFR</th>
                  <th className="num">Stars</th>
                  <th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {frames.slice(0, 40).map((f) => {
                  // AstroController decides what is worth flagging; its notes say why.
                  const flags = [
                    f.flags?.cloud_suspect && "clouds?",
                    f.flags?.tracking_suspect && "tracking?",
                    f.flags?.saturated && "saturated",
                    ...(f.flags?.notes ?? []),
                  ].filter(Boolean) as string[];
                  return (
                    <tr key={f.index}>
                      <td className="num">{f.index}</td>
                      <td className="num">{new Date(f.date).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false })}</td>
                      <td>{f.target ?? "—"}</td>
                      <td>
                        <FilterChip filter={filterLetterOf(f.filter)} /> <span className={styles.hint}>{f.filter}</span>
                      </td>
                      <td className="num">{fmt(f.exposure_s, 0, " s")}</td>
                      <td className="num">{fmt(f.hfr, 2)}</td>
                      <td className="num">{f.stars ?? "—"}</td>
                      <td>
                        {flags.length ? (
                          <Tip tip={`Stars ${f.flags?.star_drop_pct != null ? `${f.flags.star_drop_pct > 0 ? "−" : "+"}${Math.abs(Math.round(f.flags.star_drop_pct))} %` : "—"} vs recent frames · HFR ${f.flags?.hfr_excursion_pct != null ? `${f.flags.hfr_excursion_pct >= 0 ? "+" : "−"}${Math.abs(Math.round(f.flags.hfr_excursion_pct))} %` : "—"}`}>
                            <span className={styles.flagged}>
                              <StatusIcon level="warn" size={12} /> {flags.join(", ")}
                            </span>
                          </Tip>
                        ) : (
                          <span className={styles.hint}>—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className={styles.hint}>No light frames yet tonight.</p>
        )}
      </section>
    </div>
  );
}

/** RA and Dec guide error over the last steps: two series, one axis (arcsec), zero line, legend. */
function GuideChart({ graph }: { graph: { t: number[]; ra: number[]; dec: number[] } }) {
  const W = 360;
  const H = 120;
  const pad = 6;
  const lim = Math.max(1, ...graph.ra.map(Math.abs), ...graph.dec.map(Math.abs));
  const t0 = Math.min(...graph.t);
  const t1 = Math.max(...graph.t) || 1;
  const x = (t: number) => pad + ((t - t0) / (t1 - t0 || 1)) * (W - 2 * pad);
  const y = (v: number) => H / 2 - (v / lim) * (H / 2 - pad);
  const line = (vs: number[]) => vs.map((v, i) => `${i ? "L" : "M"}${x(graph.t[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <figure className={styles.guide}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="RA and Dec guide error">
        <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className={styles.zero} />
        <path d={line(graph.ra)} className={styles.raLine} />
        <path d={line(graph.dec)} className={styles.decLine} />
        <text x={W - pad} y={10} textAnchor="end" className={styles.axis}>
          ±{lim.toFixed(1)}″
        </text>
      </svg>
      <figcaption className={styles.hint}>
        <i className={styles.keyRa} /> RA <i className={styles.keyDec} /> Dec · last {Math.round(t1 - t0)} s
      </figcaption>
    </figure>
  );
}
