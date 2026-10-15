import { EngineNotice } from "@/components/EngineNotice";
import { OrganizerOverview } from "@/components/organizer/OrganizerOverview";
import { engineGet } from "@/lib/engine";
import type { OrganizerOverview as Overview } from "@/lib/organizer";
import styles from "../library.module.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Organizer" };

export default async function OrganizerPage() {
  const res = await engineGet<Overview>("/api/organizer");
  return (
    <main className="page">
      <div className={styles.head}>
        <div>
          <div className="eyebrow">Library · canonical layout</div>
          <h1 className={styles.title}>Organizer</h1>
          <p className={styles.summary}>
            Brings target folders to the canonical layout: no year level, NIGHT_ sessions, M31_Panel_1-style mosaics, FLAT under the
            night. Every change is a plan you read and approve; moves only, nothing is deleted, and every plan can be undone.
          </p>
        </div>
      </div>
      {res.ok ? <OrganizerOverview data={res.data} /> : <EngineNotice reason={res.reason} message={res.message} />}
    </main>
  );
}
