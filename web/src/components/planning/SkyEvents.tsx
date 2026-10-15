import Link from "next/link";

import { eventDetail, eventKind, eventNight, type SkyEvent } from "@/lib/telescopius";
import { localTime } from "./bits";
import { dayLabel, nightHref } from "./Nights";
import styles from "./skyEvents.module.css";

/** Sky events from Telescopius, one row each: night (links to it), time, what, detail. */
export function SkyEvents({ events, siteId, showNight = true }: { events: SkyEvent[]; siteId: string; showNight?: boolean }) {
  return (
    <ul className={styles.list}>
      {events.map((e) => {
        const night = eventNight(e);
        return (
          <li key={`${e.t}${e.name}`} className={styles.row}>
            {showNight ? (
              <Link href={nightHref(night, siteId)} className={styles.when}>
                {dayLabel(night)}
              </Link>
            ) : null}
            <span className={`${styles.time} num`}>{localTime(Date.parse(e.t))}</span>
            <span className={styles.kind}>{eventKind(e)}</span>
            <span className={styles.what}>
              {e.name}
              {eventDetail(e) ? <span className={styles.detail}> · {eventDetail(e)}</span> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
