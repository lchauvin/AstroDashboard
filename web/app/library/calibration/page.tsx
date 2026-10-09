import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { DismissControl } from "@/components/DismissControl";
import { EngineNotice } from "@/components/EngineNotice";
import { Marks, StatusIcon, Tip } from "@/components/ui";
import { engineGet } from "@/lib/engine";
import { exposure, settingsLabel } from "@/lib/format";
import type { CalibrationLibrary, DarkStatus, MasterInfo } from "@/lib/types";
import styles from "./calibration.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Calibration library" };

const STATUS_TEXT: Record<DarkStatus, string> = {
  ok: "Matched",
  unverified: "Unverified",
  "raw-only": "Raw darks only",
  missing: "No master dark",
  dismissed: "Dismissed",
};

function settings(m: { gain: number | null; offset: number | null; readout: string | null; settingsKnown?: boolean }) {
  if (m.settingsKnown === false) return "settings unknown (no matching raw frames in the build)";
  return settingsLabel(m);
}

export default async function CalibrationPage() {
  const res = await engineGet<CalibrationLibrary>("/api/calibration");
  if (!res.ok) {
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const lib = res.data;
  const masters = lib.builds.flatMap((b) => b.masters);
  const temps = [...new Set(masters.map((m) => m.temp))].sort((a, b) => (b ?? 0) - (a ?? 0));
  const exposures = [...new Set(masters.filter((m) => m.kind === "dark").map((m) => m.exposure))].sort(
    (a, b) => (a ?? 0) - (b ?? 0),
  );
  // Newest master per (kind, temp, exposure); builds are already newest-first.
  const newest = (kind: string, temp: number | null, exp: number | null): MasterInfo | undefined =>
    masters.find((m) => m.kind === kind && m.temp === temp && (kind === "bias" || m.exposure === exp));

  return (
    <main className="page">
      <Link href="/library" className={styles.back}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Library
      </Link>
      <div className="eyebrow">G:\Astro\.Calibration</div>
      <h1 className={styles.title}>Calibration library</h1>
      <p className={styles.lead}>
        Master darks and biases by set temperature and exposure, and every light setting in the Library that has no
        matching master. In Linearity HDR the camera ignores gain and offset, so those frames match on readout mode,
        exposure and temperature only.
      </p>

      <section className={styles.block}>
        <h2 className="section-title">Light settings without a master dark</h2>
        <p className={styles.note}>
          Dismiss a row when it doesn&apos;t matter (e.g. lights at −25 °C calibrated with a −30 °C dark). It stays
          listed with your note but no longer counts as a problem on any target.
        </p>
        {lib.unmatched.length ? (
          <div className={styles.scroller}>
            <table className="table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Exposure</th>
                  <th>Readout · gain · offset</th>
                  <th>Set temp</th>
                  <th>Used by</th>
                  <th style={{ textAlign: "right" }}>Frames</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {lib.unmatched.map((u) => {
                  const targets = [...new Map(u.uses.map((x) => [x.revisionId, x])).values()];
                  const nights = new Set(u.uses.map((x) => `${x.revisionId}/${x.session}`)).size;
                  return (
                    <tr key={u.key} className={u.status === "dismissed" ? styles.dismissedRow : undefined}>
                      <td className={styles.statusCell}>
                        <StatusIcon
                          level={u.status === "missing" ? "bad" : u.status === "dismissed" ? "info" : "warn"}
                        />{" "}
                        {STATUS_TEXT[u.status]}
                        {u.status === "dismissed" ? (
                          <div className={styles.dismissNote}>
                            was: {STATUS_TEXT[u.was ?? "missing"].toLowerCase()}
                            {u.note ? <> · {u.note}</> : null}
                          </div>
                        ) : null}
                      </td>
                      <td className="num">{exposure(u.exposure)}</td>
                      <td>{settingsLabel(u)}</td>
                      <td className="num">{u.setTemp != null ? `${u.setTemp} °C` : "—"}</td>
                      <td>
                        <div className={styles.uses}>
                          {targets.map((x) => (
                            <Link key={x.revisionId} href={`/library/${x.revisionId}`}>
                              {x.target}
                            </Link>
                          ))}
                        </div>
                        <div className="muted" style={{ fontSize: 12 }}>
                          {nights} night{nights > 1 ? "s" : ""}
                        </div>
                      </td>
                      <td className="num" style={{ textAlign: "right" }}>
                        {u.uses.reduce((s, x) => s + x.frames, 0)}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <DismissControl
                          issueKey={u.key}
                          label={`${exposure(u.exposure)} · ${settingsLabel(u)} · ${u.setTemp ?? "?"} °C`}
                          dismissed={u.status === "dismissed"}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p>
            <StatusIcon level="ok" /> Every light setting has a master dark.
          </p>
        )}
        {lib.unmatched.length > 0 && lib.unmatched.every((u) => u.status === "dismissed") ? (
          <p style={{ marginTop: 10 }}>
            <StatusIcon level="ok" /> Nothing left to fix — every remaining row is dismissed.
          </p>
        ) : null}
      </section>

      {lib.suspectMasters.length ? (
        <section className={styles.block}>
          <h2 className="section-title">Suspect master biases</h2>
          <p className={styles.note}>
            With this camera, bias frames must be shorter than a flat but at least {lib.minBiasExposure} s. These
            masters were built from shorter frames. Flats are calibrated with a trusted bias whenever one exists.
          </p>
          <ul className={styles.suspectList}>
            {lib.suspectMasters.map((m) => (
              <li key={`${m.build}/${m.file}`}>
                <StatusIcon level="warn" /> <span className="mono">{m.build}/{m.file}</span>{" "}
                <span className="muted">— {m.suspect}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={styles.block}>
        <h2 className="section-title">Coverage</h2>
        <p className={styles.note}>Newest master for each temperature and exposure, with its age.</p>
        <div className={styles.scroller}>
          <table className={`table ${styles.coverage}`}>
            <thead>
              <tr>
                <th>Set temp</th>
                <th>Bias</th>
                {exposures.map((e) => (
                  <th key={String(e)}>Dark {exposure(e)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {temps.map((t) => (
                <tr key={String(t)}>
                  <td className="num">{t} °C</td>
                  {[["bias", null] as const, ...exposures.map((e) => ["dark", e] as const)].map(([kind, e]) => {
                    const m = newest(kind, t, e);
                    return (
                      <td key={`${kind}-${e}`}>
                        {m ? (
                          <Tip
                            tip={
                              <span className="mono">
                                {m.file}
                                <br />
                                build {m.build} · {settings(m)}
                                {m.suspect ? (
                                  <>
                                    <br />
                                    Suspect: {m.suspect}
                                  </>
                                ) : null}
                              </span>
                            }
                          >
                            <span className={styles.coverCell}>
                              <StatusIcon
                                level={m.suspect ? "warn" : m.settingsKnown ? "ok" : "info"}
                                size={13}
                                label={m.suspect ? `Suspect: ${m.suspect}` : m.settingsKnown ? "Settings verified" : "Settings unknown"}
                              />
                              <span className="num">{m.ageDays != null ? `${m.ageDays} d` : "?"}</span>
                            </span>
                          </Tip>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.block}>
        <h2 className="section-title">Library builds</h2>
        <div className={styles.builds}>
          {lib.builds.map((b) => (
            <div key={b.build} className={`framed ${styles.build}`}>
              <Marks />
              <div className={styles.buildHead}>
                <span className={styles.buildName}>{b.build}</span>
                <span className="eyebrow">{b.ageDays != null ? `${b.ageDays} days old` : ""}</span>
              </div>
              <ul className={styles.masterList}>
                {b.masters.map((m) => (
                  <li key={m.file}>
                    {m.suspect ? <StatusIcon level="warn" size={12} label={`Suspect: ${m.suspect}`} /> : null}{" "}
                    <span className="mono">{m.file}</span>
                    <span className="muted">
                      {" "}
                      — {m.kind === "bias" ? `${exposure(m.exposure)} subs · ` : ""}
                      {settings(m)}
                      {m.sources ? ` · built from ${m.sources} frames` : ""}
                    </span>
                  </li>
                ))}
                {!b.masters.length ? <li className="muted">No masters</li> : null}
              </ul>
              {b.raw.length ? (
                <details className={styles.raw}>
                  <summary>
                    {b.raw.reduce((s, g) => s + g.frames, 0)} raw frames in {b.raw.length} groups
                  </summary>
                  <table className="table">
                    <tbody>
                      {b.raw.map((g, i) => (
                        <tr key={i}>
                          <td style={{ whiteSpace: "nowrap" }}>
                            {g.suspect ? <StatusIcon level="warn" size={12} label={`Suspect: ${g.suspect}`} /> : null}{" "}
                            {g.kind}
                          </td>
                          <td className="mono">{g.folder}</td>
                          <td className="num">{exposure(g.exposure)}</td>
                          <td className="num">{g.temp != null ? `${g.temp} °C` : "—"}</td>
                          <td className="num">{settings(g)}</td>
                          <td className="num" style={{ textAlign: "right" }}>
                            {g.frames}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
