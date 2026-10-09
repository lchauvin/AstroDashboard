import { exposure, filterColor, filterLabel, hours } from "@/lib/format";
import type { FilterDetail } from "@/lib/types";
import { StatusIcon, Tip } from "./ui";
import styles from "./FilterIntegration.module.css";

/** One bar per filter (magnitude), direct-labelled, plus flat coverage per filter. */
export function FilterIntegration({ filters, sessions }: { filters: FilterDetail[]; sessions: number }) {
  if (!filters.length) return <p className="muted">No usable lights yet.</p>;
  const max = Math.max(...filters.map((f) => f.seconds));
  return (
    <div className={styles.list}>
      {filters.map((f) => {
        const covered = f.sessionsWithFlats === f.sessions;
        return (
          <div key={f.filter} className={styles.row}>
            <span className={styles.label}>{filterLabel(f.filter)}</span>
            <Tip
              block
              tip={
                <>
                  <strong>{filterLabel(f.filter)}</strong>
                  <br />
                  <span className="num">
                    {hours(f.seconds)} · {f.frames} frames · {f.exposures.map(exposure).join(", ")}
                  </span>
                  <br />
                  {f.sessions} night{f.sessions > 1 ? "s" : ""} of {sessions}
                  {f.rejected ? ` · ${f.rejected} rejected` : ""}
                  {f.quarantined ? ` · ${f.quarantined} quarantined` : ""}
                </>
              }
            >
              <span className={styles.track}>
                <span className={styles.bar} style={{ width: `${(f.seconds / max) * 100}%`, background: filterColor(f.filter) }} />
              </span>
            </Tip>
            <span className={`num ${styles.value}`}>{hours(f.seconds)}</span>
            <span className={`num ${styles.frames}`}>{f.frames} fr</span>
            <span className={styles.flats}>
              <StatusIcon
                level={covered ? "ok" : "bad"}
                label={covered ? "Flats for every night" : "Some nights lack their own flats"}
                size={13}
              />
              <span className="num">
                {f.sessionsWithFlats}/{f.sessions}
              </span>
            </span>
          </div>
        );
      })}
      <div className={styles.foot}>
        <span>Usable hours</span>
        <span>Frames</span>
        <span>Nights with flats</span>
      </div>
    </div>
  );
}
