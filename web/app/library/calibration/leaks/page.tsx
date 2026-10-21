import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { EngineNotice } from "@/components/EngineNotice";
import { LeakIndex } from "@/components/leaks/LeakIndex";
import { engineGet } from "@/lib/engine";
import type { LeakTestSummary } from "@/lib/leaks";
import styles from "../calibration.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Light-leak finder" };

export default async function LeaksPage() {
  const res = await engineGet<{ tests: LeakTestSummary[] }>("/api/leaks");
  return (
    <main className="page">
      <Link href="/library/calibration" className={styles.back}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Calibration library
      </Link>
      <div className="eyebrow">Darks · before and after</div>
      <h1 className={styles.title}>Light-leak finder</h1>
      <p className={styles.lead}>
        Compares sets of darks taken before and after covering part of the rig. Leaks show up as structure — a gradient, a
        bright edge, a glow from one side — more than as a change of the overall mean, so each comparison looks at the frame
        levels and noise, and at the shape of the difference between the two masters.
      </p>
      {res.ok ? <LeakIndex tests={res.data.tests} /> : <EngineNotice reason={res.reason} message={res.message} />}
    </main>
  );
}
