import Link from "next/link";

import { EngineNotice } from "@/components/EngineNotice";
import { RescanButton } from "@/components/ScanControls";
import { TargetsTable, type TargetRow } from "@/components/TargetsTable";
import { engineGet } from "@/lib/engine";
import { hours, relativeTime } from "@/lib/format";
import type { Overview } from "@/lib/types";
import styles from "./library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Library" };

export default async function LibraryPage() {
  const res = await engineGet<Overview>("/api/targets");
  if (!res.ok) {
    return (
      <main className="page">
        <h1 className={styles.title}>Library</h1>
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const ov = res.data;
  const rows: TargetRow[] = ov.targets.flatMap((g) =>
    g.revisions.map((r) => ({ ...r, catalogId: g.catalogId, name: g.name, revisionCount: g.revisions.length })),
  );
  const totalSeconds = rows.reduce((s, r) => s + r.totals.seconds, 0);
  const totalNights = rows.reduce((s, r) => s + r.totals.nights, 0);

  return (
    <main className="page">
      <div className={styles.head}>
        <div>
          <div className="eyebrow">Data organization</div>
          <h1 className={styles.title}>Library</h1>
          <p className={styles.summary}>
            <span className="num">{ov.targets.length}</span> targets · <span className="num">{hours(totalSeconds)}</span> of
            usable lights · <span className="num">{totalNights}</span> nights ·{" "}
            <span className="num">{ov.frames.toLocaleString("en-CA")}</span> frames indexed
          </p>
        </div>
        <div className={styles.headActions}>
          <span className={styles.scanned}>
            Scanned {relativeTime(ov.scannedAt)} · {ov.scanDurationS}s
          </span>
          <Link href="/library/calibration" className="button">
            Calibration library
          </Link>
          <RescanButton />
        </div>
      </div>
      <TargetsTable rows={rows} />
    </main>
  );
}
