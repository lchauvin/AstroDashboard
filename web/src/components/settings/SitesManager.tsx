"use client";

import dynamic from "next/dynamic";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Download, FileUp, MapPin, Plus, Star, Trash2, Upload } from "lucide-react";

import { readFrameSite, type FrameSite } from "@/lib/planning/frameHeader";
import { BORTLE_BANDS, bortleFromSqm, distanceKm, formatLatLon, parseLatLon, sqmFromBortle } from "@/lib/planning/sites";
import type { HorizonPoint, Site } from "@/lib/planning/types";
import { Marks } from "../ui";
import type { Suggestion } from "./SitesMap";
import styles from "./settings.module.css";

const SitesMap = dynamic(() => import("./SitesMap"), { ssr: false, loading: () => <div className={styles.map} /> });

export type LearnedBin = { az: number; width: number; horizon: number | null; blocked: number };
export type SiteSky = { siteId: string; lSqm: number | null; frames: number };

type Draft = {
  id: string | null;
  name: string;
  lat: string;
  lon: string;
  elevation: string;
  sqm: string;
  minAlt: string;
  horizon: HorizonPoint[];
  isDefault: boolean;
  notes: string;
};

const emptyDraft = (lat = "", lon = ""): Draft => ({ id: null, name: "", lat, lon, elevation: "", sqm: "18", minAlt: "20", horizon: [], isDefault: false, notes: "" });
const fromSite = (s: Site): Draft => ({
  id: s.id,
  name: s.name,
  lat: String(s.lat),
  lon: String(s.lon),
  elevation: s.elevation != null ? String(s.elevation) : "",
  sqm: String(s.sqm),
  minAlt: String(s.minAlt),
  horizon: s.horizon,
  isDefault: s.isDefault,
  notes: s.notes,
});

async function api(path: string, method: string, body?: unknown) {
  const res = await fetch(`/engine${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Engine error ${res.status}`);
  return data;
}

export function SitesManager({ sites, suggestions, learned, sky }: { sites: Site[]; suggestions: Suggestion[]; learned: LearnedBin[]; sky: SiteSky[] }) {
  const router = useRouter();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [placing, setPlacing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [frame, setFrame] = useState<(FrameSite & { file: string }) | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const importRef = useRef<HTMLInputElement>(null);

  const draftLatLon = draft && draft.lat !== "" && draft.lon !== "" && Number.isFinite(Number(draft.lat)) && Number.isFinite(Number(draft.lon)) ? { lat: Number(draft.lat), lon: Number(draft.lon) } : null;
  const near = draftLatLon ? sites.find((s) => s.id !== draft?.id && distanceKm(s.lat, s.lon, draftLatLon.lat, draftLatLon.lon) <= 5) : undefined;
  const learnedBins = learned.filter((b) => b.horizon != null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      if (!draft) return;
      const body = {
        name: draft.name,
        lat: Number(draft.lat),
        lon: Number(draft.lon),
        elevation: draft.elevation === "" ? null : Number(draft.elevation),
        sqm: Number(draft.sqm),
        minAlt: Number(draft.minAlt),
        horizon: draft.horizon,
        isDefault: draft.isDefault,
        notes: draft.notes,
      };
      if (draft.id) await api(`/api/sites/${draft.id}`, "PUT", body);
      else await api("/api/sites", "POST", body);
      setDraft(null);
      setPlacing(false);
      setFrame(null);
      setMessage(`Saved ${body.name}.`);
    });

  const onFrame = async (file: File) => {
    setError(null);
    try {
      const r = await readFrameSite(file);
      setFrame({ ...r, file: file.name });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const applyFrame = (asUpdateOf?: Site) => {
    if (!frame || frame.lat == null || frame.lon == null) return;
    const base = asUpdateOf ? fromSite(asUpdateOf) : draft ?? emptyDraft();
    setDraft({
      ...base,
      name: base.name || (frame.object ? `Site of ${frame.object}` : "New site"),
      lat: frame.lat.toFixed(6),
      lon: frame.lon.toFixed(6),
      elevation: frame.elevation != null ? String(Math.round(frame.elevation)) : base.elevation,
    });
    setFrame(null);
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ sites: sites.map(({ id, nights, lastNight, ...rest }) => rest) }, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "astrodashboard-sites.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importJson = (file: File) =>
    run(async () => {
      const parsed = JSON.parse(await file.text());
      const r = await api("/api/sites/import", "POST", Array.isArray(parsed) ? { sites: parsed } : parsed);
      setMessage(`Imported: ${r.added} added, ${r.updated} updated.`);
    });

  const sqmNum = draft ? Number(draft.sqm) : NaN;

  return (
    <div className={styles.sites}>
      <div className={styles.mapCol}>
        <SitesMap
          sites={sites}
          suggestions={suggestions}
          draft={draftLatLon}
          placing={placing}
          onPlace={(lat, lon) => {
            setDraft((d) => ({ ...(d ?? emptyDraft()), lat: String(lat), lon: String(lon) }));
            setPlacing(false);
          }}
          onEdit={(s) => {
            setDraft(fromSite(s));
            setPlacing(false);
          }}
        />
        <p className={styles.hint}>
          {placing
            ? "Click on the map to drop the pin."
            : "Square pins are your sites (filled: default; size grows with nights shot). Hollow pins are suggestions from your headers. In the form, the pin can be dragged."}
        </p>
      </div>

      <div className={styles.listCol}>
        <div className={styles.toolbar}>
          <button type="button" className="button button-primary" onClick={() => { setDraft(emptyDraft()); setPlacing(true); setFrame(null); }}>
            <Plus size={13} strokeWidth={1.8} /> Add site
          </button>
          <button type="button" className="button" onClick={exportJson} title="Download every site as JSON">
            <Download size={13} strokeWidth={1.8} /> Export
          </button>
          <button type="button" className="button" onClick={() => importRef.current?.click()} title="Add or update sites from a JSON export">
            <Upload size={13} strokeWidth={1.8} /> Import
          </button>
          <input ref={importRef} type="file" accept="application/json,.json" hidden onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])} />
        </div>
        {message ? <p className={styles.ok}>{message}</p> : null}
        {error ? <p className={styles.error}>{error}</p> : null}

        {draft ? (
          <div className={`framed ${styles.form}`}>
            <Marks />
            <div className="eyebrow">{draft.id ? "Edit site" : "New site"}</div>

            <div className={styles.fromFrame}>
              <button type="button" className="button" onClick={() => fileRef.current?.click()}>
                <FileUp size={13} strokeWidth={1.8} /> From a FITS/XISF frame…
              </button>
              <span className={styles.hint}>Only the header is read, in the browser; the file is not uploaded.</span>
              <input ref={fileRef} type="file" accept=".fits,.fit,.fts,.xisf" hidden onChange={(e) => e.target.files?.[0] && onFrame(e.target.files[0])} />
            </div>
            {frame ? (
              <div className={styles.framePreview}>
                <div>
                  <strong>{frame.file}</strong> ({frame.format}){frame.object ? ` · ${frame.object}` : ""}
                  {frame.dateLoc ? ` · ${frame.dateLoc.slice(0, 16).replace("T", " ")}` : ""}
                </div>
                {frame.lat != null && frame.lon != null ? (
                  <div className="num">
                    {formatLatLon(frame.lat, frame.lon)} ({frame.keys.lat}/{frame.keys.lon}){frame.elevation != null ? ` · ${Math.round(frame.elevation)} m` : ""}
                  </div>
                ) : null}
                {frame.notes.map((n) => (
                  <div key={n} className={styles.hint}>
                    {n}
                  </div>
                ))}
                {frame.lat != null && frame.lon != null ? (
                  <div className={styles.rowActions}>
                    <button type="button" className="button" onClick={() => applyFrame()}>
                      Use these values
                    </button>
                    <button type="button" className="button" onClick={() => setFrame({ ...frame, lon: -frame.lon! })} title="Some software writes longitude west-positive">
                      Flip longitude sign
                    </button>
                    {(() => {
                      const close = sites.find((s) => distanceKm(s.lat, s.lon, frame.lat!, frame.lon!) <= 5);
                      return close && close.id !== draft.id ? (
                        <button type="button" className="button" onClick={() => applyFrame(close)}>
                          Update {close.name} instead ({distanceKm(close.lat, close.lon, frame.lat!, frame.lon!).toFixed(1)} km)
                        </button>
                      ) : null;
                    })()}
                  </div>
                ) : null}
              </div>
            ) : null}

            <label className={styles.field}>
              <span>Name</span>
              <input className={styles.input} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Mont-Mégantic" />
            </label>
            <div className={styles.grid2}>
              <label className={styles.field}>
                <span>Latitude</span>
                <input className={styles.input} value={draft.lat} inputMode="decimal" onChange={(e) => setDraft({ ...draft, lat: e.target.value })} />
              </label>
              <label className={styles.field}>
                <span>Longitude (east +)</span>
                <input className={styles.input} value={draft.lon} inputMode="decimal" onChange={(e) => setDraft({ ...draft, lon: e.target.value })} />
              </label>
            </div>
            <label className={styles.field}>
              <span>Or paste “45.544, -73.538” or a Google Maps link</span>
              <input
                className={styles.input}
                placeholder="https://www.google.com/maps/@45.4553,-71.1525,15z"
                onChange={(e) => {
                  const p = parseLatLon(e.target.value);
                  if (p) setDraft({ ...draft, lat: String(p.lat), lon: String(p.lon) });
                }}
              />
            </label>
            <div className={styles.rowActions}>
              <button type="button" className="button" onClick={() => setPlacing(true)}>
                <MapPin size={13} strokeWidth={1.8} /> Place on the map
              </button>
              {near ? <span className={styles.warn}>Within 5 km of {near.name} — edit that site instead?</span> : null}
            </div>
            <div className={styles.grid2}>
              <label className={styles.field}>
                <span>Elevation (m, optional)</span>
                <input className={styles.input} value={draft.elevation} inputMode="decimal" onChange={(e) => setDraft({ ...draft, elevation: e.target.value })} />
              </label>
              <label className={styles.field}>
                <span>Minimum altitude (°)</span>
                <input className={styles.input} value={draft.minAlt} inputMode="decimal" onChange={(e) => setDraft({ ...draft, minAlt: e.target.value })} />
              </label>
            </div>
            <div className={styles.grid2}>
              <label className={styles.field}>
                <span>Sky: SQM (mag/arcsec²)</span>
                <input className={styles.input} value={draft.sqm} inputMode="decimal" onChange={(e) => setDraft({ ...draft, sqm: e.target.value })} />
              </label>
              <label className={styles.field}>
                <span>…or Bortle class</span>
                <select
                  className={styles.input}
                  value={Number.isFinite(sqmNum) ? bortleFromSqm(sqmNum).class : 5}
                  onChange={(e) => setDraft({ ...draft, sqm: sqmFromBortle(Number(e.target.value)).toFixed(2) })}
                >
                  {BORTLE_BANDS.map((b) => (
                    <option key={b.class} value={b.class}>
                      {b.class} · {b.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {(() => {
              const m = draft.id ? sky.find((s) => s.siteId === draft.id) : undefined;
              return m?.lSqm != null ? (
                <p className={styles.hint}>
                  Your luminance frames here measure ≈ SQM {m.lSqm.toFixed(2)} (photon transfer, moonless frames preferred).{" "}
                  <button type="button" className={styles.link} onClick={() => setDraft({ ...draft, sqm: m.lSqm!.toFixed(2) })}>
                    Use it
                  </button>
                </p>
              ) : null;
            })()}

            <div className={styles.field}>
              <span>Horizon profile (optional): obstruction altitude by azimuth, interpolated</span>
              <HorizonEditor points={draft.horizon} minAlt={Number(draft.minAlt) || 0} onChange={(horizon) => setDraft({ ...draft, horizon })} />
              <div className={styles.rowActions}>
                <button
                  type="button"
                  className="button"
                  disabled={!learnedBins.length}
                  title={learnedBins.length ? "Replace the profile with the one learned from obstructed frames" : "Not enough obstructed frames yet (Diagnostics → Learned horizon)"}
                  onClick={() => {
                    const floor = Number(draft.minAlt) || 0;
                    const pts: HorizonPoint[] = [];
                    for (const b of learnedBins) {
                      pts.push({ az: b.az, alt: floor }, { az: b.az + 0.5, alt: b.horizon! }, { az: b.az + b.width - 0.5, alt: b.horizon! }, { az: (b.az + b.width) % 360, alt: floor });
                    }
                    setDraft({ ...draft, horizon: pts.sort((a, b) => a.az - b.az) });
                  }}
                >
                  Use learned horizon ({learnedBins.length} sector{learnedBins.length === 1 ? "" : "s"})
                </button>
              </div>
            </div>

            <label className={styles.field}>
              <span>Notes</span>
              <input className={styles.input} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            </label>
            <label className={styles.check}>
              <input type="checkbox" checked={draft.isDefault} onChange={(e) => setDraft({ ...draft, isDefault: e.target.checked })} /> Default site for the Planner
            </label>
            <div className={styles.rowActions}>
              <button type="button" className="button button-primary" disabled={busy || !draft.name.trim() || !draftLatLon} onClick={save}>
                {busy ? "Saving…" : "Save site"}
              </button>
              <button type="button" className="button" onClick={() => { setDraft(null); setPlacing(false); setFrame(null); }}>
                Cancel
              </button>
            </div>
          </div>
        ) : null}

        <ul className={styles.siteList}>
          {sites.map((s) => {
            const b = bortleFromSqm(s.sqm);
            const m = sky.find((x) => x.siteId === s.id);
            return (
              <li key={s.id} className={styles.siteItem}>
                <div className={styles.siteHead}>
                  <strong>{s.name}</strong>
                  {s.isDefault ? (
                    <span className={styles.tag}>
                      <Star size={11} strokeWidth={2} /> default
                    </span>
                  ) : null}
                </div>
                <div className={styles.siteMeta}>
                  {formatLatLon(s.lat, s.lon)}
                  {s.elevation != null ? ` · ${Math.round(s.elevation)} m` : ""}
                  <br />
                  SQM {s.sqm.toFixed(2)} · Bortle {b.class} · floor {s.minAlt}°{s.horizon.length ? ` + ${s.horizon.length}-point profile` : ""}
                  {m?.lSqm != null ? ` · measured ≈ ${m.lSqm.toFixed(2)}` : ""}
                  <br />
                  {s.nights ?? 0} night{s.nights === 1 ? "" : "s"} shot{s.lastNight ? ` · last ${s.lastNight}` : ""}
                  {s.notes ? <><br />{s.notes}</> : null}
                </div>
                <div className={styles.rowActions}>
                  <button type="button" className={styles.link} onClick={() => { setDraft(fromSite(s)); setPlacing(false); }}>
                    Edit
                  </button>
                  <a className={styles.link} href={`/planning?site=${s.id}`}>
                    Plan here
                  </a>
                  {!s.isDefault ? (
                    <button type="button" className={styles.link} onClick={() => run(() => api(`/api/sites/${s.id}`, "PUT", { ...s, isDefault: true }))}>
                      Make default
                    </button>
                  ) : null}
                  {sites.length > 1 ? (
                    confirmDelete === s.id ? (
                      <span className={styles.confirm}>
                        Delete {s.name}?{" "}
                        <button type="button" className={styles.link} onClick={() => run(async () => { await api(`/api/sites/${s.id}`, "DELETE"); setConfirmDelete(null); })}>
                          Yes, delete
                        </button>{" "}
                        <button type="button" className={styles.link} onClick={() => setConfirmDelete(null)}>
                          Keep
                        </button>
                      </span>
                    ) : (
                      <button type="button" className={styles.link} onClick={() => setConfirmDelete(s.id)} title="Removes the site from the dashboard (no files are touched)">
                        <Trash2 size={12} strokeWidth={1.8} /> Delete
                      </button>
                    )
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>

        <div className={styles.suggestions}>
          <h3 className="eyebrow">Suggest sites from my data</h3>
          <p className={styles.hint}>Places your headers (SITELAT/SITELONG) were shot from, clustered within 5 km.</p>
          <ul className={styles.siteList}>
            {suggestions.map((g) => (
              <li key={`${g.lat},${g.lon}`} className={styles.siteItem}>
                <div className={styles.siteMeta}>
                  {formatLatLon(g.lat, g.lon)} · {g.nights} night{g.nights === 1 ? "" : "s"}
                  {g.last ? ` · last ${g.last}` : ""}
                </div>
                {g.knownSite ? (
                  <span className={styles.hint}>Already a site: {sites.find((s) => s.id === g.knownSite)?.name}</span>
                ) : (
                  <button type="button" className={styles.link} onClick={() => setDraft({ ...emptyDraft(String(g.lat), String(g.lon)), name: "", notes: `${g.nights} nights in the library` })}>
                    Add as a site…
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

/** Points table plus a small polar preview (N up, E left, as seen looking up). */
function HorizonEditor({ points, minAlt, onChange }: { points: HorizonPoint[]; minAlt: number; onChange: (p: HorizonPoint[]) => void }) {
  const set = (i: number, patch: Partial<HorizonPoint>) => onChange(points.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const S = 120;
  const C = S / 2;
  const R = S / 2 - 6;
  const xy = (az: number, alt: number) => {
    const r = (R * (90 - Math.max(0, Math.min(90, alt)))) / 90;
    const a = (az * Math.PI) / 180;
    return [Math.round((C - r * Math.sin(a)) * 10) / 10, Math.round((C - r * Math.cos(a)) * 10) / 10];
  };
  const sorted = [...points].sort((a, b) => a.az - b.az);
  const ring = Array.from({ length: 73 }, (_, i) => i * 5).map((az) => {
    let alt = minAlt;
    if (sorted.length) {
      const hi = sorted.find((p) => p.az >= az) ?? sorted[0];
      const loIdx = sorted.indexOf(hi) - 1;
      const lo = loIdx >= 0 ? sorted[loIdx] : sorted[sorted.length - 1];
      const span = ((hi.az - lo.az) % 360 + 360) % 360 || 360;
      const off = ((az - lo.az) % 360 + 360) % 360;
      alt = Math.max(minAlt, lo.alt + ((hi.alt - lo.alt) * off) / span);
    }
    return xy(az, alt);
  });
  return (
    <div className={styles.horizon}>
      <svg width={S} height={S} role="img" aria-label="Horizon profile preview">
        <circle cx={C} cy={C} r={R} className={styles.hRing} />
        <circle cx={C} cy={C} r={(R * 60) / 90} className={styles.hRing} />
        <circle cx={C} cy={C} r={(R * 30) / 90} className={styles.hRing} />
        <path d={`M${ring.map(([x, y]) => `${x},${y}`).join(" L")} Z`} className={styles.hSky} />
        <text x={C} y={9} className={styles.hLabel} textAnchor="middle">N</text>
        <text x={6} y={C + 3} className={styles.hLabel}>E</text>
      </svg>
      <div>
        {points.map((p, i) => (
          <div key={i} className={styles.hRow}>
            <label>
              az <input className={styles.inputSmall} type="number" min={0} max={359} value={p.az} onChange={(e) => set(i, { az: Number(e.target.value) })} />
            </label>
            <label>
              alt <input className={styles.inputSmall} type="number" min={0} max={90} value={p.alt} onChange={(e) => set(i, { alt: Number(e.target.value) })} />
            </label>
            <button type="button" className={styles.link} onClick={() => onChange(points.filter((_, j) => j !== i))}>
              remove
            </button>
          </div>
        ))}
        <button type="button" className={styles.link} onClick={() => onChange([...points, { az: points.length ? (points[points.length - 1].az + 30) % 360 : 180, alt: minAlt + 10 }])}>
          + add point
        </button>
      </div>
    </div>
  );
}
