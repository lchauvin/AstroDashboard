import { filterColor, filterLabel, hours, sortFilters } from "@/lib/format";
import type { FilterTotal } from "@/lib/types";
import { Tip } from "./ui";
import styles from "./FilterBar.module.css";

/**
 * Integration per filter as one stacked horizontal bar, scaled to `maxSeconds`
 * so rows are comparable. 2px gaps between segments; each segment has a tooltip.
 */
export function FilterBar({ filters, maxSeconds }: { filters: FilterTotal[]; maxSeconds: number }) {
  const items = sortFilters(filters).filter((f) => f.seconds > 0);
  const total = items.reduce((s, f) => s + f.seconds, 0);
  const width = maxSeconds > 0 ? (total / maxSeconds) * 100 : 0;
  return (
    <div className={styles.track} aria-label={items.map((f) => `${filterLabel(f.filter)} ${hours(f.seconds)}`).join(", ")}>
      <div className={styles.bar} style={{ width: `${Math.max(width, items.length ? 1 : 0)}%` }}>
        {items.map((f) => (
          <span key={f.filter} className={styles.segWrap} style={{ flexGrow: f.seconds }}>
            <Tip
              block
              tip={
                <>
                  <strong>{filterLabel(f.filter)}</strong>
                  <br />
                  <span className="num">
                    {hours(f.seconds)} · {f.frames} frames
                  </span>
                </>
              }
            >
              <span className={styles.seg} style={{ background: filterColor(f.filter) }} />
            </Tip>
          </span>
        ))}
      </div>
    </div>
  );
}

export function FilterLegend({ filters }: { filters: string[] }) {
  return (
    <div className={styles.legend}>
      {filters.map((f) => (
        <span key={f} className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: filterColor(f) }} aria-hidden="true" />
          {filterLabel(f)}
        </span>
      ))}
    </div>
  );
}
