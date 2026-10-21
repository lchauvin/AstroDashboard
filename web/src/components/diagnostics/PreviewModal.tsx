"use client";

import { useEffect, useState } from "react";
import { Ban, Check, ChevronLeft, ChevronRight, X } from "lucide-react";

import styles from "./Diagnostics.module.css";

/** Stretched frame preview with ← / → stepping and Esc to close; K / R keep or reject when `onDecide` is given. */
export function PreviewModal({
  items,
  index: start,
  onClose,
  onDecide,
  deciding,
}: {
  items: { path: string; file: string; caption: string; rejected?: boolean }[];
  index: number;
  onClose: () => void;
  onDecide?: (path: string, reject: boolean) => void;
  deciding?: string | null;
}) {
  const [index, setIndex] = useState(start);
  const [loaded, setLoaded] = useState<string | null>(null);
  const item = items[index];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(items.length - 1, i + 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
      if (onDecide && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const key = e.key.toLowerCase();
        if (key === "k" || key === "r") onDecide(items[index].path, key === "r");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, index, onClose, onDecide]);

  const src = `/engine/api/frames/preview.jpg?path=${encodeURIComponent(item.path)}`;
  return (
    <div className={styles.scrim} role="dialog" aria-modal="true" aria-label={item.file} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        {loaded !== src ? <div className={styles.modalLoading}>Rendering preview…</div> : null}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={src}
          src={src}
          alt={item.file}
          onLoad={() => setLoaded(src)}
          style={loaded === src ? undefined : { position: "absolute", opacity: 0, pointerEvents: "none" }}
        />
        <div className={styles.modalBar}>
          <button type="button" onClick={() => setIndex(Math.max(0, index - 1))} disabled={index === 0} aria-label="Previous frame">
            <ChevronLeft size={18} />
          </button>
          <span>
            {index + 1} / {items.length}
          </span>
          <button
            type="button"
            onClick={() => setIndex(Math.min(items.length - 1, index + 1))}
            disabled={index === items.length - 1}
            aria-label="Next frame"
          >
            <ChevronRight size={18} />
          </button>
          <span style={{ flex: 1 }}>
            {item.file}
            <br />
            <span style={{ opacity: 0.7 }}>{item.caption}</span>
          </span>
          {onDecide ? (
            <button
              type="button"
              onClick={() => onDecide(item.path, !item.rejected)}
              disabled={deciding === item.path}
              title={item.rejected ? "Keep this frame anyway (K)" : "Reject this frame (R)"}
              style={{ gap: 6 }}
            >
              {item.rejected ? <Check size={16} /> : <Ban size={16} />}
              {item.rejected ? "Keep" : "Reject"}
            </button>
          ) : null}
          <button type="button" onClick={onClose} aria-label="Close preview">
            <X size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
