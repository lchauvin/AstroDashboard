"use client";

import { useEffect, useState } from "react";
import { Moon, Sun, Eye } from "lucide-react";

import styles from "./Header.module.css";

type Theme = "light" | "dark" | "night";
const ORDER: Theme[] = ["light", "dark", "night"];
const LABEL: Record<Theme, string> = { light: "Light theme", dark: "Dark theme", night: "Red night mode" };
const KEY = "astrodash-theme";

function readStored(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "night") return v;
  } catch {
    /* storage unavailable: fall back to light */
  }
  return "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("light");

  useEffect(() => {
    setTheme(readStored());
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Eye;

  return (
    <button
      type="button"
      className={styles.iconLink}
      onClick={() => {
        setTheme(next);
        try {
          localStorage.setItem(KEY, next);
        } catch {
          /* ignore */
        }
      }}
      aria-label={`${LABEL[theme]} — switch to ${LABEL[next].toLowerCase()}`}
      title={`${LABEL[theme]} (click for ${LABEL[next].toLowerCase()})`}
    >
      <Icon size={17} strokeWidth={1.6} />
    </button>
  );
}
