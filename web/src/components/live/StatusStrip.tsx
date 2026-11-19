"use client";

import { useEffect, useState } from "react";

import { filterLetterOf, type CoolerState, type DeviceFact, type FlatRow, type FlatsState, type Level, type LiveState } from "@/lib/live";
import { FilterChip, Marks, StatusIcon } from "../ui";
import styles from "./live.module.css";

/** Safety monitor, camera cooler and tonight's flats at a glance, with the buttons that open Equipment, Ingestion and Tonight's frames. */
export function StatusStrip({ s, actions }: { s: LiveState; actions: React.ReactNode }) {
  const flats = useFlats(s.frames[0]?.index);
  return (
    <section className={`framed ${styles.strip}`} aria-label="Status">
      <Marks />
      <div className={styles.stripItems}>
        <Safety s={s} />
        <Cooler s={s} />
        <Flats f={flats} />
      </div>
      <div className={styles.stripActions}>{actions}</div>
    </section>
  );
}

function Item({ label, level, children, title }: { label: string; level: Level | null; children: React.ReactNode; title?: string }) {
  return (
    <div className={styles.stripItem} title={title}>
      <span className="eyebrow">{label}</span>
      <span className={styles.stripValue}>
        {level ? <StatusIcon level={level} size={14} /> : null}
        {children}
      </span>
    </div>
  );
}

/** What NINA's safety monitor reads (the dashboard's sky monitor when NINA uses it). */
function Safety({ s }: { s: LiveState }) {
  const d = s.equipment.find((e) => e.key === "safetymonitor");
  if (!d) return <Item label="Safety monitor" level="info">{s.nina.connected ? "None connected in NINA" : "NINA not connected"}</Item>;
  const safe = d.level === "ok";
  return (
    <Item label="Safety monitor" level={safe ? "ok" : "bad"} title={d.name ?? undefined}>
      {safe ? "Safe" : "Unsafe"}
    </Item>
  );
}

function Cooler({ s }: { s: LiveState }) {
  const cam = s.equipment.find((e) => e.key === "camera");
  if (!cam) return <Item label="Camera" level="info">Not connected</Item>;
  const c = cam.cooler ?? coolerFromFacts(cam.facts);
  if (!c) return <Item label="Camera" level={cam.level}>{cam.status}</Item>;
  if (!c.on)
    return (
      <Item label="Camera" level="warn" title={cam.name ?? undefined}>
        Cooler off{c.temperature != null ? ` · ${c.temperature.toFixed(1)} °C` : ""}
      </Item>
    );
  const off = c.temperature != null && c.setPoint != null ? Math.abs(c.temperature - c.setPoint) : null;
  const level: Level = off != null && off > 1 ? "warn" : "ok";
  return (
    <Item
      label="Camera"
      level={level}
      title={
        `${cam.name ?? "Camera"}: set point ${c.setPoint != null ? `${c.setPoint.toFixed(0)} °C` : "unknown"}` +
        (level === "warn" ? " (the sensor is more than 1 °C away from it)" : "")
      }
    >
      Cooler on
      <span className={styles.hint}>
        {c.temperature != null ? ` · ${c.temperature.toFixed(1)} °C` : ""}
        {c.power != null ? ` · ${c.power.toFixed(0)} %` : ""}
      </span>
    </Item>
  );
}

/** An engine without the card's `cooler` field: read the Cooler, Sensor and Set point facts ("off", "10 %", "-10.0 °C"). */
function coolerFromFacts(facts: DeviceFact[]): CoolerState | null {
  const get = (label: string) => facts.find((f) => f.label === label)?.value;
  const cooler = get("Cooler");
  if (cooler == null) return null;
  const num = (v: string | undefined) => {
    const n = v == null ? NaN : parseFloat(v.replace("−", "-"));
    return Number.isFinite(n) ? n : null;
  };
  return { on: cooler !== "off", power: num(cooler), temperature: num(get("Sensor")), setPoint: num(get("Set point")) };
}

function Flats({ f }: { f: Flats }) {
  if (f === "error") return <Item label="Flats tonight" level="info">Engine not answering</Item>;
  if (f === "outdated") return <Item label="Flats tonight" level="info">Restart the engine to check flats</Item>;
  if (!f) return <Item label="Flats tonight" level={null}>…</Item>;
  if (!f.filters.length) return <Item label="Flats tonight" level="info">No sequence or lights yet</Item>;
  const want = f.wantReadout;
  return (
    <div className={styles.stripItem}>
      <span className="eyebrow">Flats tonight{want ? ` · ${want}` : ""}</span>
      <span className={styles.flatChips}>
        {f.filters.map((r) => (
          <span key={r.filter} className={styles.flatChip} title={flatText(r, want)}>
            <StatusIcon level={r.level} size={13} label={flatText(r, want)} />
            {r.filter === "—" ? "No filter" : <FilterChip filter={filterLetterOf(r.filter) ?? r.filter} />}
          </span>
        ))}
      </span>
    </div>
  );
}

function flatText(r: FlatRow, want: string | null): string {
  const name = r.filter === "—" ? "No filter" : r.filter;
  if (r.level === "bad") return `${name}: no flats tonight yet`;
  const parts = [`${name}: ${r.frames} flat${r.frames === 1 ? "" : "s"}`];
  if (r.readouts.length) parts.push(r.readouts.join(", "));
  if (r.wrong.length && want) parts.push(`not all in ${want} (${r.wrong.join(", ")}): this sensor's flats misbehave in other readout modes`);
  if (r.unknownReadout) parts.push(`${r.unknownReadout} without a readout mode in the header`);
  return parts.join(" · ");
}

/** `/api/live/flats` every 30 s, and at once when a new frame arrives. */
type Flats = FlatsState | null | "error" | "outdated";

function useFlats(newest: number | undefined): Flats {
  const [data, setData] = useState<Flats>(null);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const res = await fetch("/engine/api/live/flats", { cache: "no-store" });
        if (!stop) setData(res.ok ? await res.json() : res.status === 404 ? "outdated" : "error");
      } catch {
        if (!stop) setData("error");
      }
    };
    void load();
    const id = setInterval(() => !document.hidden && void load(), 30_000);
    return () => {
      stop = true;
      clearInterval(id);
    };
  }, [newest]);
  return data;
}
