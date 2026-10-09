"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RotateCw } from "lucide-react";

/** Fetches (or refreshes) the Finkbeiner Hα measurement for a target through the engine, then re-renders. */
export function HalphaButton({ ra, dec, maj, min, pa, refresh }: { ra: number; dec: number; maj: number | null; min: number | null; pa: number | null; refresh: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    const q = new URLSearchParams({ ra: String(ra), dec: String(dec), ...(maj ? { maj: String(maj) } : {}), ...(min ? { min: String(min) } : {}), ...(pa != null ? { pa: String(pa) } : {}), ...(refresh ? { refresh: "true" } : {}) });
    try {
      const res = await fetch(`/engine/api/planning/halpha?${q}`);
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Engine error ${res.status}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" className="button" onClick={run} disabled={busy}>
        <RotateCw size={12} strokeWidth={1.8} /> {busy ? "Measuring…" : refresh ? "Re-measure from the survey" : "Measure from the Hα survey"}
      </button>
      {error ? <span style={{ color: "var(--status-bad)", fontSize: 13 }}>{error}</span> : null}
    </span>
  );
}
