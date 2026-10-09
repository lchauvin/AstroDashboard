import { IngestPanel } from "@/components/live/IngestPanel";
import { LiveView } from "@/components/live/LiveView";
import { engineGet } from "@/lib/engine";
import type { IngestStatus, LivePayload } from "@/lib/live";
import { EngineNotice } from "@/components/EngineNotice";
import pageStyles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Live" };

export default async function LivePage() {
  const [live, ingest] = await Promise.all([engineGet<LivePayload>("/api/live/state"), engineGet<IngestStatus>("/api/ingest")]);
  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">Tonight · via AstroController</div>
          <h1 className={pageStyles.title}>Live</h1>
          <p className={pageStyles.summary}>
            The running sequence, the latest frame, guiding and conditions, read from AstroController — and the frames NINA
            drops in <span className="mono">.Download</span>, sorted into their targets.
          </p>
        </div>
      </div>
      {live.ok ? <LiveView initial={live.data} /> : <EngineNotice reason={live.reason} message={live.message} />}
      <section style={{ marginTop: 40 }}>
        <h2 className="section-title" style={{ marginBottom: 14 }}>
          Auto-ingest
        </h2>
        <IngestPanel initial={ingest.ok ? ingest.data : null} />
      </section>
    </main>
  );
}
