import { Placeholder } from "@/components/Placeholder";

export const metadata = { title: "Planning" };

export default function PlanningPage() {
  return (
    <Placeholder
      eyebrow="What to shoot next"
      title="Planning"
      phase="Phase 4 · not built yet"
      items={[
        "Site selector; imaging hours in astronomical darkness above each site's horizon",
        "Moon-aware filter suggestion per night; calendar of the next nights",
        "SNR vs integration per filter (vendored from AstroPlanner); goals per filter in hours or SNR",
      ]}
    />
  );
}
