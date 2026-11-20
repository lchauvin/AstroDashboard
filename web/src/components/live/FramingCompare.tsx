"use client";

import { useCallback, useRef, useState } from "react";

import { shortDate } from "@/lib/format";
import { framingText, type LiveFraming } from "@/lib/framing";
import styles from "./FramingCompare.module.css";

/** The newest light and its reference night's light, one over the other, with a bar to drag between them:
 *  left of it the reference, right of it tonight. Both stay in their own sensor pixels (nothing is aligned), so
 *  what moves as the bar passes is the framing's shift and rotation; a reference across the meridian is shown
 *  half a turn round so the two are the same way up. */
export function FramingCompare({ f }: { f: LiveFraming }) {
  const [pos, setPos] = useState(50);
  const autoTurn = Math.abs(f.turn ?? 0) > 90;
  const [turned, setTurned] = useState(autoTurn);
  const [loaded, setLoaded] = useState({ current: false, reference: false });
  const [failed, setFailed] = useState<string | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const v = encodeURIComponent(`${f.frame ?? ""}|${f.referenceFrame ?? ""}`);
  const refDate = f.previousDate ? shortDate(f.previousDate) : f.previous;

  const moveTo = useCallback((clientX: number) => {
    const r = stage.current?.getBoundingClientRect();
    if (!r || !r.width) return;
    setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  }, []);

  const ready = loaded.current && loaded.reference;
  return (
    <div className={styles.wrap}>
      <p className={styles.summary}>{framingText(f)}</p>
      <div className={styles.stageRow}>
        <div
          ref={stage}
          className={styles.stage}
          onPointerDown={(e) => {
            dragging.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            moveTo(e.clientX);
          }}
          onPointerMove={(e) => dragging.current && moveTo(e.clientX)}
          onPointerUp={() => (dragging.current = false)}
          onPointerCancel={() => (dragging.current = false)}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className={styles.base}
            src={`/engine/api/live/framing/current.jpg?v=${v}`}
            alt={`Tonight: ${f.frame ?? "newest light"}`}
            draggable={false}
            onLoad={() => setLoaded((l) => ({ ...l, current: true }))}
            onError={() => setFailed("Tonight's light could not be rendered")}
          />
          <div className={styles.overlay} style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              className={styles.overlayImg}
              style={turned ? { transform: "rotate(180deg)" } : undefined}
              src={`/engine/api/live/framing/reference.jpg?v=${v}`}
              alt={`Reference: ${f.referenceFrame ?? refDate ?? "reference night"}`}
              draggable={false}
              onLoad={() => setLoaded((l) => ({ ...l, reference: true }))}
              onError={() => setFailed("The reference light could not be rendered")}
            />
          </div>
          <span className={`${styles.tag} ${styles.tagLeft}`}>Reference · {refDate}</span>
          <span className={`${styles.tag} ${styles.tagRight}`}>Tonight</span>
          <div
            className={styles.bar}
            style={{ left: `${pos}%` }}
            role="slider"
            tabIndex={0}
            aria-label="Reference on the left, tonight on the right"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(pos)}
            onKeyDown={(e) => {
              const step = e.shiftKey ? 10 : 2;
              if (e.key === "ArrowLeft") setPos((p) => Math.max(0, p - step));
              else if (e.key === "ArrowRight") setPos((p) => Math.min(100, p + step));
              else if (e.key === "Home") setPos(0);
              else if (e.key === "End") setPos(100);
              else return;
              e.preventDefault();
            }}
          >
            <span className={styles.knob} aria-hidden="true" />
          </div>
          {!ready || failed ? <div className={styles.status}>{failed ?? "Rendering the two lights…"}</div> : null}
        </div>
      </div>
      <div className={styles.foot}>
        <span className="muted">
          Left: {f.referenceFrame ?? "?"} ({refDate}, the reference night) · Right: {f.frame ?? "?"} · drag the bar, or use ← →
        </span>
        <label className={styles.turn} title={autoTurn ? "The reference is across the meridian from tonight's light" : undefined}>
          <input type="checkbox" checked={turned} onChange={(e) => setTurned(e.target.checked)} />
          <span>Reference turned half a turn{autoTurn ? " (meridian flip)" : ""}</span>
        </label>
      </div>
    </div>
  );
}
