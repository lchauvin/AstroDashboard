import { EngineNotice } from "@/components/EngineNotice";
import { Guiding } from "@/components/live/Guiding";
import { engineGet } from "@/lib/engine";
import type { LiveState } from "@/lib/live";
import pageStyles from "../../library/library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Guiding" };

export default async function GuidingPage() {
  const live = await engineGet<LiveState>("/api/live/state?graph=300");
  return (
    <main className="page">
      <div className={pageStyles.head}>
        <div>
          <div className="eyebrow">Live · PHD2</div>
          <h1 className={pageStyles.title}>Guiding</h1>
          <p className={pageStyles.summary}>
            Guide error and its shape, the guide star, PHD2&apos;s parameters — and an optional AI advisor that suggests changes you can apply,
            each one measured against the minutes before it.
          </p>
        </div>
      </div>
      {live.ok || live.reason !== "offline" ? <Guiding initial={live.ok ? live.data : null} /> : <EngineNotice reason={live.reason} message={live.message} />}
    </main>
  );
}
