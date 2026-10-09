"use client";

import { Eye } from "lucide-react";

import type { Finding } from "@/lib/diagnostics";
import { filterLetter } from "@/lib/diagnostics";
import { FilterChip, StatusIcon } from "../ui";
import styles from "./Findings.module.css";

const clock = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** What went wrong tonight, as episodes; each can open its frames in the preview. */
export function Findings({ findings, onPreview }: { findings: Finding[]; onPreview: (paths: string[]) => void }) {
  const warn = findings.filter((f) => f.level !== "info");
  const info = findings.filter((f) => f.level === "info");
  return (
    <section className={styles.wrap}>
      <div className={styles.head}>
        <h2 className="section-title">Findings</h2>
        <span className="eyebrow">clouds · obstruction · twilight · moon · dew · focus · tracking · pointing · flats</span>
      </div>
      {!warn.length ? (
        <p className={styles.clean}>
          <StatusIcon level="ok" /> Nothing to worry about on this night.
        </p>
      ) : null}
      <ul className={styles.list}>
        {[...warn, ...info].map((f, i) => (
          <li key={`${f.code}-${i}`} className={f.level === "info" ? `${styles.item} ${styles.info}` : styles.item}>
            <StatusIcon level={f.level} />
            <div className={styles.body}>
              <div className={styles.title}>
                {f.title}
                {f.from ? (
                  <span className={styles.when}>
                    {clock(f.from)}
                    {f.to && f.to !== f.from ? `–${clock(f.to)}` : ""}
                  </span>
                ) : null}
                {f.filters.map((x) => (
                  <FilterChip key={x} filter={filterLetter(x)} />
                ))}
              </div>
              <p className={styles.text}>{f.text}</p>
            </div>
            {f.frames.length ? (
              <button type="button" className={styles.preview} onClick={() => onPreview(f.frames)}>
                <Eye size={12} strokeWidth={1.8} aria-hidden="true" /> {f.frames.length} frame{f.frames.length > 1 ? "s" : ""}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
