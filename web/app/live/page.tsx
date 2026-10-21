import { IngestPanel } from "@/components/live/IngestPanel";
import { Overview } from "@/components/live/Overview";
import { engineGet } from "@/lib/engine";
import type { IngestStatus, LiveState } from "@/lib/live";
import { EngineNotice } from "@/components/EngineNotice";
import pageStyles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Live" };

export default async function LivePage() {
  const [live, ingest] = await Promise.all([engineGet<LiveState>("/api/live/state?graph=180"), engineGet<IngestStatus>("/api/ingest")]);
  const ingestSection = (
    <section key="auto-ingest">
      <h2 className="section-title" style={{ marginBottom: 14 }}>
        Auto-ingest
      </h2>
      <IngestPanel initial={ingest.ok ? ingest.data : null} />
    </section>
  );
  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">Tonight · NINA + PHD2</div>
          <h1 className={pageStyles.title}>Live</h1>
          <p className={pageStyles.summary}>
            The sequence, the last image and the connected equipment, read straight from NINA and PHD2 — and the frames NINA drops in{" "}
            its image folder, sorted into their targets.
          </p>
        </div>
      </div>
      {/* Auto-ingest sits above Tonight's frames (inside the Overview); without the engine it stays below the notice. */}
      {live.ok || live.reason !== "offline" ? (
        <Overview initial={live.ok ? live.data : null} ingest={ingest.ok ? ingest.data : null} beforeFrames={ingestSection} />
      ) : (
        <>
          <EngineNotice reason={live.reason} message={live.message} />
          {ingestSection}
        </>
      )}
    </main>
  );
}
