"use client";

import { useState } from "react";

import { targetNameProblem } from "@/lib/organizer";
import styles from "./organizer.module.css";

type Lookup = { found: boolean; catalogId: string; name?: string | null };

/** Revision 1 of a new target: the form only prepares a draft plan, like every other organizer change. */
export function NewTarget({
  known,
  dataRoot,
  busy,
  onPrepare,
}: {
  /** Catalog ids already in the library (casefolded), which need New revision instead. */
  known: Set<string>;
  dataRoot?: string;
  busy: boolean;
  onPrepare: (body: { catalogId: string; name: string; panels: number }) => void;
}) {
  const [catalogId, setCatalogId] = useState("");
  const [name, setName] = useState("");
  const [panels, setPanels] = useState(1);

  const id = catalogId.trim().replace(/\s+/g, " ");
  const nm = name.trim().replace(/\s+/g, " ");
  const folder = `${id} (${nm})`;
  const taken = id && known.has(id.toLowerCase());
  const problem = id || nm ? (taken ? `${id} is already in the library: use New revision on it` : targetNameProblem(id, nm)) : null;

  // Fill in the canonical designation and its common name from the catalog (never overwrites a typed name).
  const lookup = async () => {
    if (!id) return;
    try {
      const res = await fetch(`/api/catalog?q=${encodeURIComponent(id)}`);
      const hit = (await res.json()) as Lookup;
      if (hit.catalogId) setCatalogId(hit.catalogId);
      if (hit.found && hit.name && !nm) setName(hit.name);
    } catch {
      // The catalog is a convenience; typing both fields works without it.
    }
  };

  return (
    <form
      className={styles.newTarget}
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem && id && nm) onPrepare({ catalogId: id, name: nm, panels });
      }}
    >
      <div className={styles.fields}>
        <label className={styles.field}>
          <span>Catalog id</span>
          <input
            className={styles.input}
            value={catalogId}
            onChange={(e) => setCatalogId(e.target.value)}
            onBlur={lookup}
            placeholder="NGC 7000"
            spellCheck={false}
          />
        </label>
        <label className={styles.field}>
          <span>Name</span>
          <input className={styles.input} value={name} onChange={(e) => setName(e.target.value)} placeholder="North America Nebula" />
        </label>
        <label className={styles.field}>
          <span>Mosaic panels</span>
          <input
            className={`${styles.input} ${styles.narrow}`}
            type="number"
            min={1}
            max={50}
            value={panels}
            onChange={(e) => setPanels(Math.min(50, Math.max(1, Math.round(Number(e.target.value) || 1))))}
          />
        </label>
        <button type="submit" className={styles.action} disabled={busy || !id || !nm || !!problem}>
          {busy ? "Preparing…" : "Prepare plan"}
        </button>
      </div>
      {problem ? (
        <p className={styles.error}>{problem}</p>
      ) : id && nm ? (
        <p className={`mono ${styles.hint}`}>
          {dataRoot ? `${dataRoot}\\` : ""}
          {folder}\ → .SessionData\ · .Calibration\ · .Finished\ · target.json
          {panels > 1 ? ` (mosaic, ${panels} panels)` : ""}
        </p>
      ) : null}
    </form>
  );
}
