/** Small presentational pieces for Planning (server-renderable: no hooks). */

import type { FilterNight } from "@/lib/planning/evaluate";
import type { GoalStatus } from "@/lib/planning/plan";
import { FILTER_NAMES, type FilterLetter } from "@/lib/planning/rig";
import { filterColor } from "@/lib/format";
import { Tip } from "../ui";
import styles from "./planning.module.css";

/** Moon disc lit by phase angle (0 new → 180 full), waxing lit on the right as seen from the north. */
export function MoonGlyph({ phase, size = 18, label }: { phase: number; size?: number; label?: string }) {
  const r = size / 2 - 1;
  const c = size / 2;
  const p = ((phase % 360) + 360) % 360;
  const waxing = p < 180;
  // Terminator ellipse half-width: +r at new, 0 at quarter, -r at full.
  const k = Math.cos((p * Math.PI) / 180) * r;
  const sweepOuter = waxing ? 1 : 0;
  const sweepInner = k > 0 ? (waxing ? 0 : 1) : waxing ? 1 : 0;
  const d = `M ${c} ${c - r} A ${r} ${r} 0 0 ${sweepOuter} ${c} ${c + r} A ${Math.abs(k).toFixed(2)} ${r} 0 0 ${sweepInner} ${c} ${c - r} Z`;
  return (
    <svg width={size} height={size} className={styles.moon} role="img" aria-label={label ?? "Moon phase"}>
      <circle cx={c} cy={c} r={r} className={styles.moonDark} />
      {p > 2 && p < 358 ? <path d={d} className={styles.moonLit} /> : null}
    </svg>
  );
}

const pct = (x: number) => `${Math.round(x * 100)} %`;

/** One bar per relevant filter: share of a moonless night's SNR² that tonight delivers. */
export function EfficiencyBars({ nights, relevant }: { nights: FilterNight[]; relevant: FilterLetter[] }) {
  const rows = relevant.map((l) => nights.find((n) => n.letter === l)).filter((n): n is FilterNight => !!n && n.hours > 0);
  if (!rows.length) return <span className="muted">—</span>;
  return (
    <div className={styles.effList}>
      {rows.map((n) => (
        <Row key={n.letter} n={n} />
      ))}
    </div>
  );
}

function Row({ n }: { n: FilterNight }) {
  const none = n.snr2Dark <= 0;
  return (
    <>
      <span className="mono" style={{ color: "var(--text-secondary)" }}>
        {n.letter}
      </span>
      <Tip
        tip={
          none
            ? `${FILTER_NAMES[n.letter]}: no modelled signal for this target`
            : `${FILTER_NAMES[n.letter]}: ${pct(n.efficiency)} of a moonless night's SNR² over ${n.hours.toFixed(1)} h · tonight SNR ${Math.sqrt(n.snr2).toFixed(1)}`
        }
      >
        <span className={styles.bar} style={{ width: 64, display: "block" }}>
          <span style={{ width: `${none ? 0 : Math.round(n.efficiency * 100)}%`, background: filterColor(n.letter) }} />
        </span>
      </Tip>
      <span className="num" style={{ color: "var(--text-secondary)" }}>
        {none ? "—" : pct(n.efficiency)}
      </span>
    </>
  );
}

export function GoalMini({ goals }: { goals: GoalStatus[] }) {
  const set = goals.filter((g) => g.goal && g.goal.mode !== "none" && g.goalHours != null);
  if (!set.length) return <span className="muted" style={{ fontSize: 12.5 }}>no goals</span>;
  return (
    <div className={styles.effList}>
      {set.map((g) => {
        const frac = g.goalHours && Number.isFinite(g.goalHours) ? Math.min(1, g.goodHours / g.goalHours) : 0;
        return (
          <span key={g.letter} style={{ display: "contents" }}>
            <span className="mono" style={{ color: "var(--text-secondary)" }}>
              {g.letter}
            </span>
            <Tip
              tip={`${FILTER_NAMES[g.letter]}: ${g.goodHours.toFixed(1)} h good of ${Number.isFinite(g.goalHours!) ? g.goalHours!.toFixed(1) : "∞"} h${g.goal?.mode === "snr" ? ` (SNR ${g.goal.snr} goal)` : ""}${g.flag ? ` — ${g.flag}` : ""}`}
            >
              <span className={styles.bar} style={{ width: 64, display: "block" }}>
                <span style={{ width: `${Math.round(frac * 100)}%`, background: "var(--color-accent)" }} />
              </span>
            </Tip>
            <span className={`num ${g.flag ? styles.flag : ""}`}>{Math.round(frac * 100)} %</span>
          </span>
        );
      })}
    </div>
  );
}

/** A year of usable hours, one bar per week; sequential single hue (more hours = more ink). */
export function SeasonStrip({ hours, start, maxHours = 10 }: { hours: number[]; start: string; maxHours?: number }) {
  const weeks: number[] = [];
  for (let i = 0; i < hours.length; i += 7) {
    const w = hours.slice(i, i + 7);
    weeks.push(w.reduce((a, b) => a + b, 0) / w.length);
  }
  const W = 3;
  const GAP = 1;
  const H = 18;
  const width = weeks.length * (W + GAP);
  const [y, m, d] = start.split("-").map(Number);
  const months: { x: number; label: string }[] = [];
  for (let i = 0; i < hours.length; i++) {
    const day = new Date(y, m - 1, d + i);
    if (day.getDate() === 1 && day.getMonth() % 3 === 0) {
      months.push({ x: Math.floor(i / 7) * (W + GAP), label: day.toLocaleDateString("en-CA", { month: "short" }) });
    }
  }
  const best = weeks.indexOf(Math.max(...weeks));
  const bestDate = new Date(y, m - 1, d + best * 7).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
  return (
    <Tip tip={`Usable hours per night over the next year (weekly mean). Best around ${bestDate}: ${weeks[best]?.toFixed(1)} h/night.`}>
      <svg width={width} height={H + 11} className={styles.season} role="img" aria-label={`Season: best around ${bestDate}`}>
        {weeks.map((h, i) => (
          <rect
            key={i}
            x={i * (W + GAP)}
            y={H - Math.max(1, (Math.min(h, maxHours) / maxHours) * H)}
            width={W}
            height={Math.max(1, (Math.min(h, maxHours) / maxHours) * H)}
            className={styles.seasonCell}
            opacity={h > 0 ? 0.35 + 0.65 * Math.min(1, h / maxHours) : 0.12}
          />
        ))}
        <line x1={0.5} x2={0.5} y1={0} y2={H} className={styles.seasonToday} />
        {months.map((mo) => (
          <text key={mo.x} x={mo.x} y={H + 10} className={styles.seasonAxis}>
            {mo.label}
          </text>
        ))}
      </svg>
    </Tip>
  );
}

export function localTime(ms: number | null): string {
  if (ms == null) return "—";
  return new Date(ms).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });
}
