"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import styles from "./live.module.css";

/** A wide dialog over the Live page: Esc, the close button or a click outside closes it; the page behind does not scroll.
 *  `advanced`: this dialog's own Advanced switch, shown in its header. */
export function LiveModal({
  title,
  onClose,
  advanced,
  children,
}: {
  title: string;
  onClose: () => void;
  advanced?: [boolean, (v: boolean) => void];
  children: React.ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);
  return (
    <div className={styles.modalScrim} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} className={styles.modal} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
        <div className={styles.modalHead}>
          <h2 className="section-title">{title}</h2>
          {advanced ? (
            <label className={`${styles.advToggle} ${styles.modalAdv}`}>
              <input type="checkbox" checked={advanced[0]} onChange={(e) => advanced[1](e.target.checked)} />
              <span>Advanced</span>
            </label>
          ) : null}
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Close">
            <X size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className={styles.modalBody}>{children}</div>
      </div>
    </div>
  );
}
