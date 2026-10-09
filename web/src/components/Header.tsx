"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Settings } from "lucide-react";

import { ThemeToggle } from "./ThemeToggle";
import styles from "./Header.module.css";

const SECTIONS = [
  { href: "/library", label: "Library" },
  { href: "/diagnostics", label: "Diagnostics" },
  { href: "/live", label: "Live" },
  { href: "/planning", label: "Planning" },
];

export function Header() {
  const pathname = usePathname() ?? "/";
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link href="/library" className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true" />
          <span className={styles.brandName}>AstroDashboard</span>
          <span className={styles.brandTagline}>Observatory log</span>
        </Link>
        <nav className={styles.nav} aria-label="Sections">
          {SECTIONS.map((s) => {
            const active = pathname.startsWith(s.href);
            return (
              <Link
                key={s.href}
                href={s.href}
                className={active ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink}
                aria-current={active ? "page" : undefined}
              >
                {s.label}
              </Link>
            );
          })}
          <span className={styles.divider} aria-hidden="true" />
          <ThemeToggle />
          <Link
            href="/settings"
            className={pathname.startsWith("/settings") ? `${styles.iconLink} ${styles.iconLinkActive}` : styles.iconLink}
            aria-label="Settings"
            title="Settings"
          >
            <Settings size={17} strokeWidth={1.6} />
          </Link>
        </nav>
      </div>
    </header>
  );
}
