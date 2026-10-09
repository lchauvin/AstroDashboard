import { DiagnosticsOverview } from "@/components/diagnostics/DiagnosticsOverview";
import { HorizonPlot } from "@/components/diagnostics/HorizonPlot";
import { EngineNotice } from "@/components/EngineNotice";
import { engineGet } from "@/lib/engine";
import type { DiagJob, DiagResultRow, HorizonData, SessionIndexRow } from "@/lib/diagnostics";
import styles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Diagnostics" };

export default async function DiagnosticsPage() {
  const [sessions, diag, horizon] = await Promise.all([
    engineGet<{ sessions: SessionIndexRow[] }>("/api/sessions"),
    engineGet<{ jobs: DiagJob[]; results: DiagResultRow[] }>("/api/diagnostics"),
    engineGet<HorizonData>("/api/horizon"),
  ]);
  if (!sessions.ok || !diag.ok) {
    const bad = !sessions.ok ? sessions : !diag.ok ? diag : null;
    return (
      <main className="page">
        <h1 className={styles.title}>Diagnostics</h1>
        {bad && !bad.ok ? <EngineNotice reason={bad.reason} message={bad.message} /> : null}
      </main>
    );
  }
  return (
    <main className="page">
      <div className={styles.head}>
        <div>
          <div className="eyebrow">Session quality · astro-eval</div>
          <h1 className={styles.title}>Diagnostics</h1>
          <p className={styles.summary}>
            Measures every light and flat of a night (FWHM, stars, background, gradient, transparency, trails,
            obstruction) and scores them. Runs in the background; results are kept in the dashboard, nothing is
            written to the data folder.
          </p>
        </div>
      </div>
      {horizon.ok && horizon.data.samples ? <HorizonPlot data={horizon.data} /> : null}
      <DiagnosticsOverview sessions={sessions.data.sessions} initialJobs={diag.data.jobs} initialResults={diag.data.results} />
    </main>
  );
}
