import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";

import { SessionDiagnostics } from "@/components/diagnostics/SessionDiagnostics";
import { EngineNotice } from "@/components/EngineNotice";
import { engineGet } from "@/lib/engine";
import type { DiagDetail } from "@/lib/diagnostics";
import { hours, shortDate } from "@/lib/format";
import targetStyles from "../../../library/[rev]/target.module.css";

export const dynamic = "force-dynamic";

type Params = Promise<{ rev: string; session: string }>;

export async function generateMetadata({ params }: { params: Params }) {
  const { rev, session } = await params;
  return { title: `Diagnostics · ${rev} · ${decodeURIComponent(session)}` };
}

export default async function SessionDiagnosticsPage({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Promise<{ tab?: string; panel?: string; run?: string }>;
}) {
  const { rev, session } = await params;
  const { tab, panel, run } = await searchParams;
  const folder = decodeURIComponent(session);
  const res = await engineGet<DiagDetail>(`/api/diagnostics/${encodeURIComponent(rev)}/${encodeURIComponent(folder)}`);
  if (!res.ok) {
    if (res.reason === "not-found") notFound();
    return (
      <main className="page">
        <EngineNotice reason={res.reason} message={res.message} />
      </main>
    );
  }
  const d = res.data;
  const idx = d.sessions.indexOf(folder);
  const prev = idx > 0 ? d.sessions[idx - 1] : null;
  const next = idx >= 0 && idx < d.sessions.length - 1 ? d.sessions[idx + 1] : null;
  const link = (f: string) => `/diagnostics/${rev}/${encodeURIComponent(f)}`;

  return (
    <main className="page">
      <Link href="/diagnostics" className={targetStyles.back}>
        <ChevronLeft size={14} strokeWidth={1.8} /> Diagnostics
      </Link>
      <div className="eyebrow">
        <Link href={`/library/${d.revision.id}`}>{d.revision.folder}</Link> · {folder}
      </div>
      <h1 className={targetStyles.title}>
        {shortDate(d.session.date)}
        <span className={targetStyles.titleName}>
          {d.revision.catalogId} · {d.session.totals.lights} lights · {hours(d.session.totals.seconds)}
        </span>
      </h1>
      <div className={targetStyles.meta}>
        {prev ? (
          <Link href={link(prev)} className="button">
            ← {prev}
          </Link>
        ) : null}
        {next ? (
          <Link href={link(next)} className="button">
            {next} →
          </Link>
        ) : null}
      </div>
      <SessionDiagnostics key={folder} detail={d} initialSection={tab} initialPanel={panel} autoRun={run === "1"} />
    </main>
  );
}
