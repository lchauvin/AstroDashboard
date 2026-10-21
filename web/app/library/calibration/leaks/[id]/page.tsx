import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { EngineNotice } from "@/components/EngineNotice";
import { LeakTestView } from "@/components/leaks/LeakTestView";
import { engineGet } from "@/lib/engine";
import type { LeakTest } from "@/lib/leaks";
import styles from "../../calibration.module.css";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await engineGet<LeakTest>(`/api/leaks/${encodeURIComponent(id)}`);
  return { title: res.ok ? `${res.data.name} · light leaks` : "Light-leak finder" };
}

export default async function LeakTestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await engineGet<LeakTest>(`/api/leaks/${encodeURIComponent(id)}`);
  return (
    <main className="page">
      <Link href="/library/calibration/leaks" className={styles.back}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Light-leak finder
      </Link>
      {res.ok ? <LeakTestView initial={res.data} /> : <EngineNotice reason={res.reason} message={res.message} />}
    </main>
  );
}
