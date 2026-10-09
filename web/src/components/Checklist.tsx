import type { Check } from "@/lib/types";
import { StatusIcon } from "./ui";
import styles from "./Checklist.module.css";

export function Checklist({ checks }: { checks: Check[] }) {
  if (!checks.length) return <p className="muted">Nothing to check yet.</p>;
  return (
    <ul className={styles.list}>
      {checks.map((c) => (
        <li key={c.code} className={styles.item}>
          {c.detail.length ? (
            <details>
              <summary className={styles.summary}>
                <StatusIcon level={c.level} />
                <span className={styles.text}>{c.text}</span>
                <span className={styles.count}>{c.detail.length}</span>
              </summary>
              <ul className={styles.detail}>
                {c.detail.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </details>
          ) : (
            <div className={styles.summary}>
              <StatusIcon level={c.level} />
              <span className={styles.text}>{c.text}</span>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
