"use client";

import "leaflet/dist/leaflet.css";

import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";

import type { Site } from "@/lib/planning/types";
import { bortleFromSqm, formatLatLon } from "@/lib/planning/sites";
import styles from "./settings.module.css";

export type Suggestion = { lat: number; lon: number; nights: number; last: string | null; knownSite: string | null };

/** Square pins in the accent colour (square corners, like the rest of the design); default is filled + larger. */
const pin = (kind: "site" | "default" | "suggestion" | "draft", nights = 0) =>
  L.divIcon({
    className: "",
    html: `<span class="${styles.pin} ${styles[`pin_${kind}`]}" style="--pin:${kind === "default" ? 16 : 11 + Math.min(5, Math.round(Math.log2(1 + nights)))}px"></span>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });

/**
 * OpenStreetMap standard tiles (no API key; CARTO's basemaps now require one), muted
 * by a CSS filter to match the Industry style: greyscale in light, inverted in dark/night.
 */
function useTheme(): "light" | "dark" {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const read = () => {
      const t = document.documentElement.dataset.theme;
      setTheme(t === "dark" || t === "night" ? "dark" : "light");
    };
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return theme;
}

function ClickToPlace({ onPlace }: { onPlace?: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      onPlace?.(Number(e.latlng.lat.toFixed(5)), Number(e.latlng.lng.toFixed(5)));
    },
  });
  return null;
}

function FitTo({ points, focus }: { points: [number, number][]; focus: [number, number] | null }) {
  const map = useMap();
  const key = JSON.stringify(points);
  useEffect(() => {
    if (focus) map.setView(focus, Math.max(map.getZoom(), 9));
    else if (points.length === 1) map.setView(points[0], 8);
    else if (points.length > 1) map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 9 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, focus?.[0], focus?.[1]]);
  return null;
}

export default function SitesMap({
  sites,
  suggestions,
  draft,
  placing,
  onPlace,
  onEdit,
}: {
  sites: Site[];
  suggestions: Suggestion[];
  draft: { lat: number; lon: number } | null;
  placing: boolean;
  onPlace: (lat: number, lon: number) => void;
  onEdit: (site: Site) => void;
}) {
  const theme = useTheme();
  const points = useMemo<[number, number][]>(() => sites.map((s) => [s.lat, s.lon]), [sites]);
  return (
    <div className={`${styles.map} ${placing ? styles.mapPlacing : ""} ${theme === "dark" ? styles.mapDark : styles.mapLight}`}>
      <MapContainer center={points[0] ?? [45.5, -73.5]} zoom={8} scrollWheelZoom className={styles.mapInner} attributionControl>
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={19}
        />
        <FitTo points={points} focus={draft ? [draft.lat, draft.lon] : null} />
        <ClickToPlace onPlace={placing ? onPlace : undefined} />
        {sites.map((s) => {
          const b = bortleFromSqm(s.sqm);
          return (
            <Marker key={s.id} position={[s.lat, s.lon]} icon={pin(s.isDefault ? "default" : "site", s.nights ?? 0)} title={s.name}>
              <Popup>
                <div className={styles.popup}>
                  <strong>{s.name}</strong>
                  {s.isDefault ? <span className={styles.popupTag}>default</span> : null}
                  <div>{formatLatLon(s.lat, s.lon)}{s.elevation != null ? ` · ${Math.round(s.elevation)} m` : ""}</div>
                  <div>
                    SQM {s.sqm.toFixed(1)} · Bortle {b.class} · floor {s.minAlt}°{s.horizon.length ? " + profile" : ""}
                  </div>
                  <div>
                    {s.nights ?? 0} night{s.nights === 1 ? "" : "s"} shot{s.lastNight ? ` · last ${s.lastNight}` : ""}
                  </div>
                  <div className={styles.popupActions}>
                    <button type="button" onClick={() => onEdit(s)}>
                      Edit
                    </button>
                    <a href={`/planning?site=${s.id}`}>Plan here</a>
                  </div>
                </div>
              </Popup>
            </Marker>
          );
        })}
        {suggestions
          .filter((g) => !g.knownSite)
          .map((g) => (
            <Marker key={`${g.lat},${g.lon}`} position={[g.lat, g.lon]} icon={pin("suggestion", g.nights)} title={`Suggested: ${g.nights} nights`} />
          ))}
        {draft ? (
          <Marker
            position={[draft.lat, draft.lon]}
            icon={pin("draft")}
            draggable
            eventHandlers={{
              dragend(e) {
                const ll = (e.target as L.Marker).getLatLng();
                onPlace(Number(ll.lat.toFixed(5)), Number(ll.lng.toFixed(5)));
              },
            }}
            title="Drag to fine-tune"
          />
        ) : null}
      </MapContainer>
    </div>
  );
}
