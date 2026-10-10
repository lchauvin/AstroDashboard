"use client";

import type { DiagResult, FlatSetResult, Note } from "@/lib/diagnostics";
import { filterLetter } from "@/lib/diagnostics";
import { num } from "@/lib/format";
import type { Level } from "@/lib/types";
import { FilterChip, StatusIcon } from "../ui";
import type { CalEntry } from "./scope";
import styles from "./Diagnostics.module.css";

const NOTE_LEVEL: Record<Note["level"], Level> = { ok: "ok", info: "info", warn: "warn", bad: "bad" };
export const flatLevel = (f: FlatSetResult): Level =>
  f.summary?.verdict === "good" ? "ok" : f.summary?.verdict === "warn" ? "warn" : "bad";

/** Lights ↔ flats (header and pixel checks merged), then the flat sets themselves. */
export function Calibration({ result, entries, flats, mosaic }: { result: DiagResult; entries: CalEntry[]; flats: FlatSetResult[]; mosaic: boolean }) {
  return (
    <>
      {entries.length ? (
        <section className={styles.block}>
          <div className={styles.blockHead}>
            <h2 className="section-title">Do the flats fit the lights?</h2>
            <span className="eyebrow">settings from the headers · dust and vignetting from the pixels</span>
          </div>
          {result.calibration?.common.map((n) => (
            <p key={n.text} className={styles.calCommon}>
              <StatusIcon level={NOTE_LEVEL[n.level]} size={13} /> {n.text}
            </p>
          ))}
          {entries.map((e, i) => (
            <div key={i} className={styles.calRow}>
              <StatusIcon level={e.level} size={14} />
              <span>
                <FilterChip filter={filterLetter(e.filter)} long /> <span className="muted num">· {e.lights}</span>
                {mosaic && e.panels.length ? <span className={styles.calPanels}>{e.panels.join(", ")}</span> : null}
              </span>
              <span className="mono" style={{ fontSize: 12 }}>
                {e.flat ?? "no flats"}
              </span>
              <ul className={styles.calIssues}>
                {e.issues.map((n) => (
                  <li key={n.text}>
                    <span>{n.text}</span>
                  </li>
                ))}
                {!e.issues.length ? (
                  <li className="muted" title={e.pixelOk?.join("\n")}>
                    {e.pixelOk ? "Matching settings; the flats correct vignetting." : "Matching settings."}
                  </li>
                ) : null}
              </ul>
            </div>
          ))}
        </section>
      ) : null}

      {flats.length ? (
        <section className={styles.block}>
          <div className={styles.blockHead}>
            <h2 className="section-title">Flat sets</h2>
            <span className="eyebrow">master SNR = per-flat SNR × √n</span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className={`table ${styles.flatTable}`}>
              <thead>
                <tr>
                  <th aria-label="Verdict" />
                  <th>Filter</th>
                  <th>Set</th>
                  <th className="num">Kept</th>
                  <th className="num">Level</th>
                  <th className="num">SNR</th>
                  <th className="num">Master</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {flats.map((f) => {
                  const notes = (f.summary?.notes ?? []).filter((n) => n.level !== "info");
                  return (
                    <tr key={f.key}>
                      <td>
                        <StatusIcon level={flatLevel(f)} size={13} label={f.summary?.verdict} />
                      </td>
                      <td>
                        <FilterChip filter={filterLetter(f.filter)} />
                      </td>
                      <td className="mono">{f.key.split("· ").pop()}</td>
                      <td className="num">{f.summary ? `${f.summary.kept}/${f.summary.n}` : "—"}</td>
                      <td className="num">{f.summary?.level != null ? `${Math.round(f.summary.level * 100)}%` : "—"}</td>
                      <td className="num">{num(f.summary?.snr ?? null)}</td>
                      <td className="num">{num(f.summary?.masterSnr ?? null)}</td>
                      <td className={styles.flatNotesCell}>
                        {notes.length ? notes.map((n) => <div key={n.text}>{n.text}</div>) : <span className="muted">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  );
}
