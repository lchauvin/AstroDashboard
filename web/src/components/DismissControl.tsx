"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EyeOff, RotateCcw } from "lucide-react";

import styles from "./DismissControl.module.css";

/**
 * Dismiss / restore an issue. Dismissals are stored by the engine in its own
 * database (var/), never in the data folder; a dismissed issue is still listed
 * but no longer counted as a problem.
 */
export function DismissControl({
  issueKey,
  label,
  dismissed,
}: {
  issueKey: string;
  label: string;
  dismissed: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(method: "POST" | "DELETE") {
    setBusy(true);
    setError(null);
    try {
      const res =
        method === "POST"
          ? await fetch("/engine/api/dismissals", {
              method,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ key: issueKey, label, note }),
            })
          : await fetch(`/engine/api/dismissals?key=${encodeURIComponent(issueKey)}`, { method });
      if (!res.ok) throw new Error(`engine returned ${res.status}`);
      setOpen(false);
      setNote("");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (dismissed) {
    return (
      <button type="button" className={styles.link} onClick={() => send("DELETE")} disabled={busy}>
        <RotateCcw size={12} strokeWidth={1.8} aria-hidden="true" /> Restore
      </button>
    );
  }

  if (!open) {
    return (
      <button type="button" className={styles.link} onClick={() => setOpen(true)}>
        <EyeOff size={12} strokeWidth={1.8} aria-hidden="true" /> Dismiss
      </button>
    );
  }

  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault();
        send("POST");
      }}
    >
      <input
        className={styles.input}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Why? e.g. used the −30 °C dark"
        aria-label={`Note for dismissing ${label}`}
        maxLength={1000}
        autoFocus
      />
      <span className={styles.actions}>
        <button type="submit" className="button button-primary" disabled={busy}>
          Dismiss
        </button>
        <button type="button" className={styles.link} onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </button>
      </span>
      {error ? <span className={styles.error}>Could not save: {error}</span> : null}
    </form>
  );
}
