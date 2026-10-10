"use client";

import { Eye } from "lucide-react";

import { filterLetter } from "@/lib/diagnostics";
import { FilterChip, StatusIcon } from "../ui";
import type { FindingGroup } from "./scope";
import styles from "./Findings.module.css";

const clock = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
const span = (from: string | null, to: string | null) => (from ? `${clock(from)}${to && to !== from ? `–${clock(to)}` : ""}` : "");

function PreviewButton({ frames, of, onPreview }: { frames: string[]; of?: number; onPreview: (paths: string[]) => void }) {
  if (!frames.length) return null;
  return (
    <button type="button" className={styles.preview} onClick={() => onPreview(frames)}>
      <Eye size={12} strokeWidth={1.8} aria-hidden="true" />
      {of && of > frames.length ? `${frames.length} of ${of}` : frames.length} frame{frames.length > 1 ? "s" : ""}
    </button>
  );
}

// "2 frames: stars elongated (eccentricity up to 0.57), all …" → the shared explanation and the episode's numbers.
const COUNT = /^\d+ frames?:?\s*/;
const PAREN = /\s*\(([^)]*\d[^)]*)\)/;
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
const general = (text: string) => cap(text.replace(COUNT, "").replace(PAREN, ""));
const specific = (text: string) => PAREN.exec(text)?.[1] ?? "";

function Item({ g, onPreview }: { g: FindingGroup; onPreview: (paths: string[]) => void }) {
  // Several timed episodes (e.g. tracking at 22:10 and 01:40): one explanation, then the episodes.
  const episodes = g.items.length > 1 && g.items.some((f) => f.frames.length) ? g.items : null;
  // Same sentence apart from counts and numbers in brackets: say it once, list only the numbers.
  const shared = episodes && new Set(episodes.map((f) => general(f.text))).size === 1 ? general(episodes[0].text) : null;
  return (
    <li className={g.level === "info" ? `${styles.item} ${styles.info}` : styles.item}>
      <StatusIcon level={g.level} />
      <div className={styles.body}>
        <div className={styles.title}>
          {g.title}
          {episodes ? <span className={styles.count}>×{episodes.length}</span> : null}
          <span className={styles.when}>{span(g.from, g.to)}</span>
          {g.filters.map((x) => (
            <FilterChip key={x} filter={filterLetter(x)} />
          ))}
        </div>
        {shared ? <p className={styles.text}>{shared}</p> : null}
        {episodes ? (
          <details className={styles.episodes}>
            <summary>
              {episodes.length} episodes · {g.frames.length} frames
            </summary>
            <ul>
              {episodes.map((f, i) => (
                <li key={i} title={f.text}>
                  <span className={styles.when}>{span(f.from, f.to)}</span>
                  <span>{shared ? specific(f.text) : cap(f.text.replace(COUNT, ""))}</span>
                  <PreviewButton frames={f.frames} onPreview={onPreview} />
                </li>
              ))}
            </ul>
          </details>
        ) : (
          <p className={styles.text}>{g.texts[0]}</p>
        )}
      </div>
      <PreviewButton frames={g.frames} of={g.allFrames} onPreview={onPreview} />
    </li>
  );
}

/** What went wrong tonight, one line per kind of problem; minor notes fold away. */
export function Findings({ groups, onPreview }: { groups: FindingGroup[]; onPreview: (paths: string[]) => void }) {
  const warn = groups.filter((g) => g.level !== "info");
  const info = groups.filter((g) => g.level === "info");
  return (
    <section className={styles.wrap}>
      <div className={styles.head}>
        <h2 className="section-title">Findings</h2>
      </div>
      {!warn.length ? (
        <p className={styles.clean}>
          <StatusIcon level="ok" /> Nothing to worry about.
        </p>
      ) : (
        <ul className={styles.list}>
          {warn.map((g) => (
            <Item key={g.code} g={g} onPreview={onPreview} />
          ))}
        </ul>
      )}
      {info.length ? (
        <details className={styles.minor}>
          <summary>
            {info.length} minor note{info.length > 1 ? "s" : ""}: {info.map((g) => g.title.toLowerCase()).join(", ")}
          </summary>
          <ul className={styles.list}>
            {info.map((g) => (
              <Item key={g.code} g={g} onPreview={onPreview} />
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
