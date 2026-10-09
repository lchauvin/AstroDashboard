"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";

import type { EngineStatus } from "@/lib/types";
import styles from "./ScanControls.module.css";

async function fetchStatus(): Promise<EngineStatus | null> {
  try {
    const res = await fetch("/engine/api/status", { cache: "no-store" });
    return res.ok ? ((await res.json()) as EngineStatus) : null;
  } catch {
    return null;
  }
}

function progressText(s: EngineStatus["scan"]): string {
  if (s.phase === "reading headers" && s.to_read) return `Reading headers ${s.read} / ${s.to_read}`;
  if (s.phase === "listing") return "Listing files…";
  return s.phase.charAt(0).toUpperCase() + s.phase.slice(1);
}

/** Polls while a scan runs, then refreshes the server-rendered page. */
function useScanWatch(active: boolean, onDone: () => void) {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!active) return;
    let stop = false;
    const tick = async () => {
      const s = await fetchStatus();
      if (stop) return;
      setStatus(s);
      if (s && !s.scan.running && s.ready) {
        done.current();
        return;
      }
      setTimeout(tick, 800);
    };
    tick();
    return () => {
      stop = true;
    };
  }, [active]);
  return status;
}

export function RescanButton() {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const status = useScanWatch(running, () => {
    setRunning(false);
    router.refresh();
  });
  return (
    <span className={styles.wrap}>
      {running && status ? <span className={styles.progress}>{progressText(status.scan)}</span> : null}
      <button
        type="button"
        className="button"
        disabled={running}
        onClick={async () => {
          setRunning(true);
          try {
            await fetch("/engine/api/scan", { method: "POST" });
          } catch {
            setRunning(false);
          }
        }}
      >
        <RefreshCw size={13} strokeWidth={1.8} className={running ? styles.spin : undefined} />
        {running ? "Scanning" : "Rescan"}
      </button>
    </span>
  );
}

/** Shown while the engine's first scan runs: live progress, then reloads itself. */
export function FirstScanProgress() {
  const router = useRouter();
  const status = useScanWatch(true, () => router.refresh());
  return (
    <p className={styles.progress}>
      {status ? progressText(status.scan) : "Waiting for the engine…"}
      {status?.error ? ` — ${status.error}` : null}
    </p>
  );
}
