"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EyeOff, RotateCcw } from "lucide-react";

import styles from "./SessionMatrix.module.css";

/**
 * Leave a night out of its target (totals, goals, checks, framing reference), or bring it back.
 * Stored by the engine in its own database; the night's files are not touched.
 */
export function IgnoreSession({ revId, folder, ignored }: { revId: string; folder: string; ignored: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/engine/api/revisions/${encodeURIComponent(revId)}/sessions/${encodeURIComponent(folder)}/ignored`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ignored: !ignored }),
      });
      if (!res.ok) throw new Error(`engine returned ${res.status}`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={styles.ignore}
        onClick={toggle}
        disabled={busy}
        title={
          ignored
            ? "Count this night again: its time, its checks, and as the framing reference"
            : "Leave this night out: not in the totals or checks, and not the framing reference for the next night. Its files stay where they are."
        }
      >
        {ignored ? <RotateCcw size={12} strokeWidth={1.8} aria-hidden="true" /> : <EyeOff size={12} strokeWidth={1.8} aria-hidden="true" />}
        {ignored ? "Add back" : "Ignore"}
      </button>
      {error ? <span className={styles.ignoreError}>{error}</span> : null}
    </>
  );
}
