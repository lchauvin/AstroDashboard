"use client";

import { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import type { Site } from "@/lib/planning/types";
import styles from "./planning.module.css";

const STORAGE_KEY = "astrodash-planner-site";

/** Site selector, start date and options. State lives in the URL; the last site is remembered per browser. */
export function PlanControls({ sites, siteId, date, showFinished, showDate = true }: { sites: Site[]; siteId: string; date: string; showFinished?: boolean; showDate?: boolean }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const go = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params?.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") next.delete(k);
      else next.set(k, v);
    }
    router.push(`${pathname}?${next.toString()}`);
  };

  // Restore the last chosen site when the URL does not name one.
  useEffect(() => {
    if (params?.get("site")) {
      try {
        localStorage.setItem(STORAGE_KEY, params.get("site")!);
      } catch {}
      return;
    }
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch {}
    if (stored && stored !== siteId && sites.some((s) => s.id === stored)) go({ site: stored });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={styles.controls}>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Site</span>
        <select className={styles.input} value={siteId} onChange={(e) => go({ site: e.target.value })}>
          {sites.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
              {s.isDefault ? " (default)" : ""} · SQM {s.sqm.toFixed(1)}
            </option>
          ))}
        </select>
      </label>
      {showDate ? (
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Night of</span>
          <input type="date" className={styles.input} value={date} onChange={(e) => go({ date: e.target.value || null })} />
        </label>
      ) : null}
      {showFinished !== undefined ? (
        <label className={styles.check}>
          <input type="checkbox" checked={showFinished} onChange={(e) => go({ finished: e.target.checked ? "1" : null })} />
          Include finished targets
        </label>
      ) : null}
    </div>
  );
}
