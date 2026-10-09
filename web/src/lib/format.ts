export const FILTER_ORDER = ["L", "R", "G", "B", "H", "O", "S"];

const FILTER_LABELS: Record<string, string> = { H: "Hα", O: "OIII", S: "SII" };

export function filterLabel(f: string | null | undefined): string {
  if (!f) return "?";
  return FILTER_LABELS[f] ?? f;
}

export function filterColor(f: string | null | undefined): string {
  return f && FILTER_ORDER.includes(f) ? `var(--filter-${f})` : "var(--filter-other)";
}

export function sortFilters<T extends { filter: string | null }>(items: T[]): T[] {
  const rank = (f: string | null) => {
    const i = f ? FILTER_ORDER.indexOf(f) : -1;
    return i === -1 ? 99 : i;
  };
  return [...items].sort((a, b) => rank(a.filter) - rank(b.filter));
}

/** 12.25 h → "12.3 h"; under an hour → "45 min". */
export function hours(seconds: number): string {
  if (seconds <= 0) return "0 h";
  const h = seconds / 3600;
  if (h < 1) return `${Math.round(seconds / 60)} min`;
  return `${h >= 10 ? h.toFixed(1) : h.toFixed(2).replace(/0$/, "")} h`;
}

export function hoursNumber(seconds: number): string {
  return (seconds / 3600).toFixed(1);
}

export function exposure(s: number | null | undefined): string {
  if (s == null) return "?";
  return `${Number.isInteger(s) ? s : s.toFixed(2)} s`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return d.toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return `${Math.round(diff / 86400)} d ago`;
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}

/** "Linearity HDR" when the mode ignores gain/offset (both null), else "Full Resolution · gain 69 · offset 1". */
export function settingsLabel(s: { readout: string | null; gain: number | null; offset: number | null }): string {
  const parts: string[] = [];
  if (s.readout) parts.push(s.readout);
  if (s.gain != null || s.offset != null) parts.push(`gain ${num(s.gain)} · offset ${num(s.offset)}`);
  return parts.join(" · ") || "settings unknown";
}

export function stageLabel(stage: string): string {
  return { acquiring: "Acquiring", processed: "WBPP done", finished: "Finished", processing: "Processing" }[stage] ?? stage;
}
