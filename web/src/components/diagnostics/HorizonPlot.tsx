"use client";

import { useState } from "react";

import type { HorizonData } from "@/lib/diagnostics";
import { Marks } from "../ui";
import styles from "./HorizonPlot.module.css";

const SIZE = 300;
const C = SIZE / 2;
const R = SIZE / 2 - 26;

/** Altitude → radius (90° at the centre, 0° at the rim); azimuth: N up, E left (looking up at the sky). */
function polar(az: number, alt: number): [number, number] {
  const r = (R * (90 - Math.max(0, Math.min(90, alt)))) / 90;
  const a = (az * Math.PI) / 180;
  // Rounded: server (Node) and browser may differ in the last digit of sin/cos, which breaks hydration.
  return [Math.round((C - r * Math.sin(a)) * 100) / 100, Math.round((C - r * Math.cos(a)) * 100) / 100];
}

export function HorizonPlot({ data }: { data: HorizonData }) {
  const [hover, setHover] = useState<string | null>(null);
  const learned = data.bins.filter((b) => b.horizon != null);

  // Learned horizon as filled wedges from the rim up to the obstructed altitude.
  const wedges = learned.map((b) => {
    const steps = 6;
    const outer = Array.from({ length: steps + 1 }, (_, i) => polar(b.az + (b.width * i) / steps, 0));
    const inner = Array.from({ length: steps + 1 }, (_, i) => polar(b.az + b.width - (b.width * i) / steps, b.horizon!));
    return { b, d: "M" + [...outer, ...inner].map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" L") + " Z" };
  });

  return (
    <div className={`framed ${styles.card}`}>
      <Marks />
      <div className={styles.text}>
        <h2 className="section-title">Learned horizon</h2>
        <p>
          Built from every analysed night: each frame where part of the field went dark while the sky was clear marks an
          obstruction at the telescope&apos;s azimuth and altitude. Two such frames in a 10° sector set the horizon
          there. Use it as a site's horizon profile in Settings → Sites.
        </p>
        <p className="muted mono" style={{ fontSize: 11 }}>
          {data.nights} night{data.nights === 1 ? "" : "s"} · {data.samples} frames with pointing · {data.obstructed} obstructed
          {learned.length ? ` · ${learned.length} sector${learned.length > 1 ? "s" : ""} learned` : " · not enough obstructed frames yet"}
        </p>
        {hover ? <p className={styles.hover}>{hover}</p> : null}
      </div>
      <svg width={SIZE} height={SIZE} role="img" aria-label="Sky map of analysed frames and learned obstructions" className={styles.svg}>
        {[0, 30, 60].map((alt) => (
          <circle key={alt} cx={C} cy={C} r={(R * (90 - alt)) / 90} className={styles.ring} />
        ))}
        {[0, 90, 180, 270].map((az) => {
          const [x, y] = polar(az, 0);
          return <line key={az} x1={C} y1={C} x2={x} y2={y} className={styles.ring} />;
        })}
        {(
          [
            ["N", 0],
            ["E", 90],
            ["S", 180],
            ["W", 270],
          ] as const
        ).map(([label, az]) => {
          const [x, y] = polar(az, -12);
          return (
            <text key={label} x={x} y={y} className={styles.cardinal} textAnchor="middle" dominantBaseline="middle">
              {label}
            </text>
          );
        })}
        {[30, 60].map((alt) => {
          const [x, y] = polar(0, alt);
          return (
            <text key={alt} x={x + 3} y={y - 2} className={styles.altLabel}>
              {alt}°
            </text>
          );
        })}
        {wedges.map(({ b, d }) => (
          <path
            key={b.az}
            d={d}
            className={styles.wedge}
            onMouseEnter={() =>
              setHover(
                `Az ${b.az}–${b.az + b.width}°: blocked up to ${b.horizon}° (${b.blocked} frames)` +
                  (b.clearMinAlt != null ? `, clear from ${b.clearMinAlt}°` : ""),
              )
            }
            onMouseLeave={() => setHover(null)}
          />
        ))}
        {data.points
          .filter((p) => !p.obstructed)
          .map((p, i) => {
            const [x, y] = polar(p.az, p.alt);
            return <circle key={`c${i}`} cx={x} cy={y} r={1.6} className={styles.clear} />;
          })}
        {data.points
          .filter((p) => p.obstructed)
          .map((p, i) => {
            const [x, y] = polar(p.az, p.alt);
            return (
              <circle
                key={`o${i}`}
                cx={x}
                cy={y}
                r={4}
                className={styles.blocked}
                onMouseEnter={() => setHover(`Obstructed frame at az ${p.az.toFixed(0)}°, alt ${p.alt.toFixed(0)}°`)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}
      </svg>
      <div className={styles.legend}>
        <span>
          <svg width="10" height="10" aria-hidden="true">
            <circle cx="5" cy="5" r="2" className={styles.clear} />
          </svg>
          clear frame
        </span>
        <span>
          <svg width="10" height="10" aria-hidden="true">
            <circle cx="5" cy="5" r="4" className={styles.blocked} />
          </svg>
          obstructed frame
        </span>
        <span>
          <span className={styles.wedgeSwatch} aria-hidden="true" />
          learned horizon
        </span>
      </div>
    </div>
  );
}
