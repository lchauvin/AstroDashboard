"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";

import { Marks } from "../ui";
import type { LeakTest, LeakTestSummary } from "@/lib/leaks";
import styles from "./leaks.module.css";

function day(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function LeakIndex({ tests }: { tests: LeakTestSummary[] }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/engine/api/leaks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (!res.ok) throw new Error(`engine returned ${res.status}`);
      const t = (await res.json()) as LeakTest;
      router.push(`/library/calibration/leaks/${t.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed");
      setBusy(false);
    }
  }

  return (
    <div className={styles.root}>
      <ol className={styles.steps}>
        <li>
          <strong>Phase 1, find the leaks:</strong> scope capped, room dark, one exposure per spot with a flashlight sweeping over it,
          plus a few with the flashlight off. Each frame is checked on its own: which spots let light in?
        </li>
        <li>
          <strong>Phase 2, fix them:</strong> a set of darks with the rig as it is (the baseline), then a set after each fix — foil,
          tape — with a comment saying what changed. Each set is compared with the one before it: no difference means the fix
          didn&apos;t help; darker or flatter means it did.
        </li>
      </ol>

      <form className={`framed ${styles.newTest}`} onSubmit={create}>
        <Marks />
        <div className={styles.row}>
          <label className={`${styles.field} ${styles.grow}`}>
            <span>New test</span>
            <input
              className={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. RedCat 51 + miniCam8M, October"
              maxLength={200}
            />
          </label>
          <button type="submit" className="button button-primary" disabled={busy || !name.trim()}>
            <Plus size={13} strokeWidth={2} aria-hidden="true" /> Start
          </button>
        </div>
        {error ? <p className={styles.error}>Could not create the test: {error}</p> : null}
      </form>

      <section className={styles.block}>
        <h2 className="section-title" style={{ marginBottom: 14 }}>
          Tests
        </h2>
        {tests.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>Test</th>
                <th>Latest change</th>
                <th className="num" style={{ textAlign: "right" }}>
                  Spot checks
                </th>
                <th className="num" style={{ textAlign: "right" }}>
                  Sets
                </th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {tests.map((t) => (
                <tr key={t.id}>
                  <td>
                    <Link href={`/library/calibration/leaks/${t.id}`} className={styles.comment}>
                      {t.name}
                    </Link>
                  </td>
                  <td className={t.lastComment ? undefined : "muted"}>{t.lastComment || "—"}</td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {t.checks}
                  </td>
                  <td className="num" style={{ textAlign: "right" }}>
                    {t.sets}
                  </td>
                  <td className="muted">{day(t.created)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No tests yet.</p>
        )}
      </section>
    </div>
  );
}
