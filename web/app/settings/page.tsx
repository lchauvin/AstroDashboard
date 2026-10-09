import Link from "next/link";

import { EngineNotice } from "@/components/EngineNotice";
import { LiveIngestSettings } from "@/components/settings/LiveIngestSettings";
import { GoalCapForm, RigForm } from "@/components/settings/RigForm";
import { SitesManager, type LearnedBin, type SiteSky } from "@/components/settings/SitesManager";
import type { Suggestion } from "@/components/settings/SitesMap";
import styles from "@/components/settings/settings.module.css";
import { engineGet } from "@/lib/engine";
import type { IngestStatus, LivePayload } from "@/lib/live";
import { loadPlanningData } from "@/server/planning";
import pageStyles from "../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Settings" };

const TABS = [
  { id: "sites", label: "Sites" },
  { id: "rig", label: "Rig" },
  { id: "goals", label: "Goals" },
  { id: "live", label: "Live · Ingest" },
] as const;

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab: rawTab } = await searchParams;
  const tab = TABS.some((t) => t.id === rawTab) ? rawTab! : "sites";
  const [data, suggest, horizon] = await Promise.all([
    loadPlanningData(),
    engineGet<{ suggestions: Suggestion[] }>("/api/sites/suggest"),
    engineGet<{ bins: LearnedBin[] }>("/api/horizon"),
  ]);
  const [live, ingest] =
    tab === "live" ? await Promise.all([engineGet<LivePayload>("/api/live/state"), engineGet<IngestStatus>("/api/ingest")]) : [null, null];

  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">Configuration · stored in the dashboard, never in the data folder</div>
          <h1 className={pageStyles.title}>Settings</h1>
        </div>
      </div>
      <nav className={styles.tabs} aria-label="Settings sections">
        {TABS.map((t) => (
          <Link key={t.id} href={`/settings?tab=${t.id}`} className={tab === t.id ? `${styles.tab} ${styles.tabOn}` : styles.tab} aria-current={tab === t.id ? "page" : undefined}>
            {t.label}
          </Link>
        ))}
      </nav>

      {!data.ok ? (
        <EngineNotice reason={data.reason} message={data.message} />
      ) : tab === "sites" ? (
        <SitesManager
          sites={data.data.context.sites}
          suggestions={suggest.ok ? suggest.data.suggestions : []}
          learned={horizon.ok ? horizon.data.bins : []}
          sky={data.data.context.sites.map(
            (s): SiteSky => ({ siteId: s.id, lSqm: data.data.calibration[s.id]?.filters.L?.sqmEquivalent ?? null, frames: data.data.calibration[s.id]?.frames ?? 0 }),
          )}
        />
      ) : tab === "rig" ? (
        <RigForm
          saved={data.data.context.settings.rig}
          detected={data.data.context.detectedRig}
          resolved={{
            telescope: data.data.rig.telescope.name,
            camera: data.data.rig.camera.name,
            pixelScale: data.data.rig.pixelScale,
            aperture: data.data.rig.telescope.apertureMm,
            focal: data.data.rig.telescope.focalLengthMm,
            readNoise: data.data.rig.camera.readNoiseE,
          }}
        />
      ) : tab === "goals" ? (
        <div style={{ maxWidth: 760 }}>
          <p className={styles.hint} style={{ marginBottom: 16 }}>
            Goals are set per target and filter on each target&apos;s planning page. An SNR goal is flagged when the hours it
            implies exceed this cap, or the usable hours left in the next 90 nights.
          </p>
          <GoalCapForm cap={data.data.context.settings.goalCapHours} />
        </div>
      ) : (
        <LiveIngestSettings
          liveUrl={data.data.context.settings.liveUrl ?? "http://127.0.0.1:3005"}
          tokenSet={live?.ok ? live.data.tokenSet : false}
          online={live?.ok ? live.data.online : false}
          ingest={ingest?.ok ? ingest.data : null}
        />
      )}
    </main>
  );
}
