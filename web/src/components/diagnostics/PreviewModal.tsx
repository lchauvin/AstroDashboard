"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import styles from "./Diagnostics.module.css";

/** Stretched frame preview with ← / → stepping and Esc to close. */
export function PreviewModal({
  items,
  index: start,
  onClose,
}: {
  items: { path: string; file: string; caption: string }[];
  index: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(start);
  const [loaded, setLoaded] = useState<string | null>(null);
  const item = items[index];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setIndex((i) => Math.min(items.length - 1, i + 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items.length, onClose]);

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
          <button type="button" onClick={onClose} aria-label="Close preview">
            <X size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
