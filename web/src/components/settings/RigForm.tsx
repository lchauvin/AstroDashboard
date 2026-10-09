"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { CAMERA_PRESETS, TELESCOPE_PRESETS } from "@/lib/snr";
import { FILTER_LETTERS, FILTER_NAMES, FILTER_PRESET_CHOICES, DEFAULT_FILTER_PRESETS, type FilterLetter } from "@/lib/planning/rig";
import type { DetectedRig, RigSettings } from "@/lib/planning/types";
import { FilterChip } from "../ui";
import styles from "./settings.module.css";

async function put(body: unknown) {
  const res = await fetch("/engine/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
}

/** Settings → Rig: what the SNR model uses. Empty fields follow the presets matched from your headers. */
export function RigForm({ saved, detected, resolved }: { saved: RigSettings | null; detected: DetectedRig | null; resolved: { telescope: string; camera: string; pixelScale: number; aperture: number; focal: number; readNoise: number } }) {
  const router = useRouter();
  const [rig, setRig] = useState<RigSettings>(saved ?? {});
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<RigSettings>) => {
    setRig({ ...rig, ...patch });
    setState("idle");
  };
  const num = (v: string) => (v === "" ? undefined : Number(v));

  return (
    <div className={styles.rig}>
      <p className={styles.hint}>
        From your headers (last year of lights): {detected ? `${detected.telescop ?? "?"} · ${detected.focalLength ?? "?"} mm · ${detected.instrume ?? "?"} · ${detected.pixelSizeUm ?? "?"} µm · filters ${detected.filters.join(", ")}` : "nothing yet"}.
        In use: {resolved.telescope} ({resolved.aperture} mm, {resolved.focal} mm) + {resolved.camera} → {resolved.pixelScale.toFixed(2)}″/px, read noise {resolved.readNoise} e⁻.
      </p>
      <div className={styles.grid2}>
        <label className={styles.field}>
          <span>Telescope preset</span>
          <select className={styles.input} value={rig.telescopeId ?? ""} onChange={(e) => set({ telescopeId: e.target.value || undefined })}>
            <option value="">Match from headers</option>
            {TELESCOPE_PRESETS.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} ({t.apertureMm} mm f/{(t.focalLengthMm / t.apertureMm).toFixed(1)})
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Camera preset</span>
          <select className={styles.input} value={rig.cameraId ?? ""} onChange={(e) => set({ cameraId: e.target.value || undefined })}>
            <option value="">Match from headers</option>
            {CAMERA_PRESETS.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.sensor}, {c.pixelSizeUm} µm)
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>Aperture (mm, override)</span>
          <input className={styles.input} inputMode="decimal" placeholder={String(resolved.aperture)} value={rig.apertureMm ?? ""} onChange={(e) => set({ apertureMm: num(e.target.value) })} />
        </label>
        <label className={styles.field}>
          <span>Focal length (mm, override)</span>
          <input className={styles.input} inputMode="decimal" placeholder={String(resolved.focal)} value={rig.focalLengthMm ?? ""} onChange={(e) => set({ focalLengthMm: num(e.target.value) })} />
        </label>
        <label className={styles.field}>
          <span>Read noise (e⁻, at your gain)</span>
          <input className={styles.input} inputMode="decimal" placeholder={String(resolved.readNoise)} value={rig.readNoiseE ?? ""} onChange={(e) => set({ readNoiseE: num(e.target.value) })} />
        </label>
      </div>

      <table className="table" style={{ marginTop: 18 }}>
        <thead>
          <tr>
            <th>Filter</th>
            <th>Model preset (bandwidth)</th>
            <th className="num">Sub length (s)</th>
          </tr>
        </thead>
        <tbody>
          {FILTER_LETTERS.map((l: FilterLetter) => (
            <tr key={l}>
              <td>
                <FilterChip filter={l} /> {FILTER_NAMES[l]}
              </td>
              <td>
                <select
                  className={styles.input}
                  value={rig.filterPresets?.[l] ?? DEFAULT_FILTER_PRESETS[l]}
                  onChange={(e) => set({ filterPresets: { ...rig.filterPresets, [l]: e.target.value } })}
                  disabled={FILTER_PRESET_CHOICES[l].length < 2}
                >
                  {FILTER_PRESET_CHOICES[l].map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name} · {f.fwhmNm} nm
                    </option>
                  ))}
                </select>
              </td>
              <td className="num">
                <input
                  className={styles.inputSmall}
                  type="number"
                  min={1}
                  placeholder={String(detected?.subSeconds?.[l] ?? "")}
                  value={rig.subSeconds?.[l] ?? ""}
                  onChange={(e) => {
                    const next = { ...rig.subSeconds };
                    if (e.target.value === "") delete next[l];
                    else next[l] = Number(e.target.value);
                    set({ subSeconds: next });
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className={styles.rowActions} style={{ marginTop: 14 }}>
        <button
          type="button"
          className="button button-primary"
          disabled={state === "saving"}
          onClick={async () => {
            setState("saving");
            setError(null);
            try {
              await put({ rig });
              setState("saved");
              router.refresh();
            } catch (e) {
              setError(e instanceof Error ? e.message : String(e));
              setState("idle");
            }
          }}
        >
          {state === "saving" ? "Saving…" : "Save rig"}
        </button>
        {state === "saved" ? <span className={styles.ok}>Saved.</span> : null}
        {error ? <span className={styles.error}>{error}</span> : null}
        <span className={styles.hint}>Empty sub lengths use the most common exposure per filter in your headers.</span>
      </div>
    </div>
  );
}

export function GoalCapForm({ cap }: { cap: number }) {
  const router = useRouter();
  const [value, setValue] = useState(String(cap));
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className={styles.rowActions}>
      <label className={styles.field} style={{ maxWidth: 260 }}>
        <span>Flag SNR goals implying more than (hours per filter)</span>
        <input className={styles.input} type="number" min={1} max={500} value={value} onChange={(e) => setValue(e.target.value)} />
      </label>
      <button
        type="button"
        className="button button-primary"
        onClick={async () => {
          try {
            await put({ goalCapHours: Number(value) });
            setMsg("Saved.");
            router.refresh();
          } catch (e) {
            setMsg(e instanceof Error ? e.message : String(e));
          }
        }}
      >
        Save
      </button>
      {msg ? <span className={styles.hint}>{msg}</span> : null}
    </div>
  );
}
