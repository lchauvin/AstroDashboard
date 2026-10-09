"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { DiagJob, DiagResultRow } from "@/lib/diagnostics";

/** Diagnostics jobs + stored results, polled while anything is queued or running. */
export function useDiagnostics(initialJobs: DiagJob[], initialResults: DiagResultRow[]) {
  const [jobs, setJobs] = useState(initialJobs);
  const [results, setResults] = useState(initialResults);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/engine/api/diagnostics", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as { jobs: DiagJob[]; results: DiagResultRow[] };
      setJobs(body.jobs);
      setResults(body.results);
    } catch {
      /* engine briefly unreachable: keep the last state */
    }
  }, []);

  const active = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!active) return;
    timer.current = setTimeout(refresh, 1000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [active, jobs, refresh]);

  const run = useCallback(
    async (revisionId: string, session?: string) => {
      await fetch("/engine/api/diagnostics/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revisionId, session }),
      });
      await refresh();
    },
    [refresh],
  );

  return { jobs, results, run, refresh };
}

export function latestJob(jobs: DiagJob[], revisionId: string, session: string): DiagJob | undefined {
  return [...jobs].reverse().find((j) => j.revisionId === revisionId && j.session === session);
}

export function jobLabel(j: DiagJob): string {
  if (j.status === "queued") return "Queued";
  if (j.status === "failed") return "Failed";
  if (j.status === "done") return "Done";
  if (j.phase === "measuring" && j.total) return `Measuring ${j.done}/${j.total}`;
  return j.phase.charAt(0).toUpperCase() + j.phase.slice(1);
}
