import Link from "next/link";
import { Droplets } from "lucide-react";

import { FilterChip, Tip } from "@/components/ui";
import type { NightQuality } from "@/lib/planning/quality";
import { FILTER_NAMES } from "@/lib/planning/rig";
import type { RatedNight } from "@/server/planning";
import { MoonGlyph, localTime } from "./bits";
import styles from "./overview.module.css";

export const nightHref = (date: string, siteId: string) => `/planning/night/${date}?site=${siteId}`;

export const dayLabel = (date: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) =>
  new Date(`${date}T12:00:00`).toLocaleDateString("en-CA", opts);

/** Coloured dot + verdict. The colour is a hint; the word carries the meaning (the night theme is red-only). */
export function QualityBadge({ q, large = false }: { q: NightQuality; large?: boolean }) {
  return (
    <span className={`${styles.badge} ${large ? styles.badgeLarge : ""}`} data-level={q.level}>
      <span className={styles.dot} aria-hidden />
      {q.label}
    </span>
  );
}

export function NightDetails({ q }: { q: NightQuality }) {
  return (
    <ul className={styles.tipList}>
      {q.details.map((d) => (
        <li key={d}>{d}</li>
      ))}
    </ul>
  );
}

/** Dew warning for a night worth setting up (red nights get none: nobody is going out). */
export function DewWarning({ q, long = false }: { q: NightQuality; long?: boolean }) {
  if (q.dewFrom == null || q.level === "poor") return null;
  const from = localTime(q.dewFrom);
  return (
    <Tip tip={`The temperature comes within 2° of the dew point from about ${from}: run the dew heaters, and cap the scope between targets.`}>
      <span className={styles.dew}>
        <Droplets size={13} aria-hidden />
        {long ? `Dew risk from ${from}` : "Dew risk"}
      </span>
    </Tip>
  );
}

/** A compact tile for the 7-night strip: day, verdict, hours. Everything else is in the tooltip. */
export function NightTile({ n, siteId }: { n: RatedNight; siteId: string }) {
  const { night, quality } = n;
  const best = night.best;
  return (
    <Tip
      block
      tip={
        <>
          <strong>{dayLabel(night.date, { weekday: "long", month: "long", day: "numeric" })}</strong>
          <NightDetails q={quality} />
          {best && quality.level !== "poor" ? (
            <div>
              Best target: {best.title} in {FILTER_NAMES[best.letter]}
            </div>
          ) : null}
        </>
      }
    >
      <Link href={nightHref(night.date, siteId)} className={styles.tile} data-level={quality.level}>
        <span className={styles.tileDay}>
          {dayLabel(night.date)}
          <MoonGlyph phase={night.moonPhase} size={14} />
        </span>
        <QualityBadge q={quality} />
        <span className={styles.tileMeta}>{quality.summary}</span>
        {quality.dewFrom != null && quality.level !== "poor" ? (
          <span className={styles.dew}>
            <Droplets size={12} aria-hidden />
            Dew risk
          </span>
        ) : null}
      </Link>
    </Tip>
  );
}

export function BestPick({ n, siteId }: { n: RatedNight; siteId: string }) {
  const best = n.night.best;
  if (!best) return null;
  return (
    <Link href={`/planning/target/${encodeURIComponent(best.key)}?site=${siteId}&date=${n.night.date}`} className={styles.pick}>
      <FilterChip filter={best.letter} />
      <span>
        {best.title} <span className={styles.muted}>· {FILTER_NAMES[best.letter]}</span>
      </span>
    </Link>
  );
}
