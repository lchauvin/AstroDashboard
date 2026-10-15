"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { LiveState } from "@/lib/live";

/** Polls the engine's live snapshot. `graph` = seconds of guide history to include. */
export function useLive(initial: LiveState | null, graph = 180, intervalMs = 2000) {
  const [data, setData] = useState<LiveState | null>(initial);
  const [offline, setOffline] = useState(false);
  const graphRef = useRef(graph);
  graphRef.current = graph;

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/engine/api/live/state?graph=${graphRef.current}`, { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const loop = async () => {
      // Pause while the tab is hidden: a phone in a pocket should not poll all night.
      if (!document.hidden) await refresh();
      if (!stop) timer = setTimeout(loop, intervalMs);
    };
    timer = setTimeout(loop, intervalMs);
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  useEffect(() => {
    void refresh();
  }, [graph, refresh]);

  return { data, offline, refresh };
}

const ADV_KEY = "astrodash.live.advanced";
const ADV_EVENT = "astrodash:advanced";

/** Advanced mode: a per-browser preference shared by every Live component on the page. */
export function useAdvanced(): [boolean, (v: boolean) => void] {
  const [adv, setAdv] = useState(false);
  useEffect(() => {
    try {
      setAdv(localStorage.getItem(ADV_KEY) === "1");
    } catch {}
    const on = (e: Event) => setAdv((e as CustomEvent<boolean>).detail);
    window.addEventListener(ADV_EVENT, on);
    return () => window.removeEventListener(ADV_EVENT, on);
  }, []);
  const set = useCallback((v: boolean) => {
    try {
      localStorage.setItem(ADV_KEY, v ? "1" : "0");
    } catch {}
    window.dispatchEvent(new CustomEvent(ADV_EVENT, { detail: v }));
  }, []);
  return [adv, set];
}
