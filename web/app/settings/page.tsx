import { Placeholder } from "@/components/Placeholder";

export const metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <Placeholder
      eyebrow="Configuration"
      title="Settings"
      phase="Arrives with the sections that need it"
      items={[
        "Sites: map with pins, add from a FITS frame, horizon profile (Phase 4)",
        "Data: data root, filter aliases — currently in astrodashboard.toml",
        "Auto-ingest mode and NINA object → target mapping (Phase 5)",
        "Rig, goal cap, AstroController URL",
      ]}
    />
  );
}
