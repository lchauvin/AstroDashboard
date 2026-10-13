import { exposure, filterColor, filterLabel, hours } from "@/lib/format";
import type { FilterDetail } from "@/lib/types";
import { StatusIcon, Tip } from "./ui";
import styles from "./FilterIntegration.module.css";

/**
 * One bar per filter (magnitude), direct-labelled, plus flat coverage per filter. Once diagnostics
 * has analysed any night, a Usable column shows what is left after its rejections; the bar draws
 * the usable part solid over the faint acquired length.
 */
export function FilterIntegration({ filters, sessions }: { filters: FilterDetail[]; sessions: number }) {
  if (!filters.length) return <p className="muted">No lights yet.</p>;
  const max = Math.max(...filters.map((f) => f.seconds));
  const diagnosed = filters.some((f) => (f.analysedSeconds ?? 0) > 0);
  const rowClass = diagnosed ? `${styles.row} ${styles.withUsable}` : styles.row;
  return (
    <div className={styles.list}>
      {filters.map((f) => {
        const covered = f.sessionsWithFlats === f.sessions;
        const usable = f.acceptedSeconds ?? f.seconds;
        const analysed = f.analysedSeconds ?? 0;
        return (
          <div key={f.filter} className={rowClass}>
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
                  {analysed ? (
                    <>
                      <br />
                      <span className="num">
                        {hours(usable)} usable after diagnostics
                        {analysed < f.seconds ? ` (${hours(analysed)} of ${hours(f.seconds)} analysed)` : ""}
                      </span>
                    </>
                  ) : null}
                </>
              }
            >
              <span className={styles.track}>
                {diagnosed ? (
                  <span
                    className={`${styles.bar} ${styles.barAcquired}`}
                    style={{ width: `${(f.seconds / max) * 100}%`, background: filterColor(f.filter) }}
                  />
                ) : null}
                <span
                  className={styles.bar}
                  style={{ width: `${((diagnosed ? usable : f.seconds) / max) * 100}%`, background: filterColor(f.filter) }}
                />
              </span>
            </Tip>
            <span className={`num ${styles.value}`}>{hours(f.seconds)}</span>
            {diagnosed ? (
              <span className={`num ${styles.value}`}>{analysed ? hours(usable) : <span className="muted">{hours(usable)}</span>}</span>
            ) : null}
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
      <div className={diagnosed ? `${styles.foot} ${styles.withUsable}` : styles.foot}>
        <span>Acquired hours</span>
        {diagnosed ? <span>Usable hours</span> : null}
        <span>Frames</span>
        <span>Nights with flats</span>
      </div>
    </div>
  );
}
