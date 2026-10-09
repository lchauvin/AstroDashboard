import { Placeholder } from "@/components/Placeholder";

export const metadata = { title: "Live" };

export default function LivePage() {
  return (
    <Placeholder
      eyebrow="Tonight"
      title="Live"
      phase="Phase 5 · not built yet"
      items={[
        "AstroController state and SSE stream (sequence, last frame, guiding RMS, conditions)",
        "Auto-ingest from G:\Astro\.Download (Off / Dry-run / Auto), with target mapping and held-file alerts",
        "Tonight's cheap diagnostics on every ingested frame",
      ]}
    />
  );
}
