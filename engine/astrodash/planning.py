"""Planning support in the engine: sites, rig detection and the sky measured from your own subs.

The planner itself (ephemeris, SNR model, ranking) runs in the web app on the
vendored AstroPlanner modules. The engine supplies what only it can see: the
headers, the diagnostics measurements, and the dashboard's own database.

Nothing here writes to the data root. Sites, goals and settings live in var/.
"""

from __future__ import annotations

import math
import re
import statistics
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .frames import Frame, parse_time

EARTH_RADIUS_KM = 6371.0
# Sessions within this distance of a site are "shot there"; also the duplicate check.
SITE_RADIUS_KM = 5.0

_SEXAGESIMAL = re.compile(r"^\s*([+-]?)\s*(\d{1,3})[\s:°d]+(\d{1,2})(?:[\s:'′m]+(\d{1,2}(?:\.\d+)?))?\s*[\"″s]?\s*([NSEWnsew])?\s*$")

LAT_KEYS = ("SITELAT", "LAT-OBS", "OBSGEO-B")
LON_KEYS = ("SITELONG", "LONG-OBS", "OBSGEO-L")
ELEV_KEYS = ("SITEELEV", "ALT-OBS", "OBSGEO-H")


# ---------------------------------------------------------------------------- sites


def parse_angle(value: Any) -> float | None:
    """Decimal degrees from a number, a decimal string or a sexagesimal string ("45 32 39", "-73:32:15 W")."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    m = _SEXAGESIMAL.match(text)
    if not m:
        return None
    sign, d, mi, s, hemi = m.groups()
    deg = int(d) + int(mi) / 60 + (float(s) if s else 0.0) / 3600
    if sign == "-" or (hemi and hemi.upper() in "SW"):
        deg = -deg
    return deg


def _first(header: dict[str, Any], keys: Iterable[str]) -> tuple[float | None, str | None]:
    for k in keys:
        v = parse_angle(header.get(k))
        if v is not None:
            return v, k
    return None, None


def site_from_header(header: dict[str, Any]) -> dict[str, Any]:
    """Location fields from a frame header, with the keyword each came from.

    An elevation of exactly 0 is "not set" (NINA writes SITEELEV = 0.0 when unknown).
    """
    lat, lat_key = _first(header, LAT_KEYS)
    lon, lon_key = _first(header, LON_KEYS)
    elev, elev_key = _first(header, ELEV_KEYS)
    notes = []
    if elev is not None and elev == 0:
        notes.append(f"{elev_key} is 0, which usually means the capture software did not know it; elevation left empty.")
        elev = None
    if lat is not None and not -90 <= lat <= 90:
        notes.append(f"{lat_key} = {lat} is not a latitude.")
        lat = None
    if lon is not None and lon > 180:
        lon -= 360
    return {
        "lat": lat,
        "lon": lon,
        "elevation": elev,
        "keys": {"lat": lat_key, "lon": lon_key, "elevation": elev_key},
        "object": header.get("OBJECT"),
        "dateLoc": header.get("DATE-LOC") or header.get("DATE-OBS"),
        "notes": notes,
    }


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest_site(sites: list[dict[str, Any]], lat: float | None, lon: float | None, max_km: float = SITE_RADIUS_KM):
    if lat is None or lon is None:
        return None
    best = None
    for s in sites:
        d = distance_km(lat, lon, s["lat"], s["lon"])
        if d <= max_km and (best is None or d < best[1]):
            best = (s, d)
    return best[0] if best else None


def session_locations(library) -> list[dict[str, Any]]:
    """Every session with lights and a header location: (lat, lon, date, target)."""
    out = []
    for rev in library.revisions.values():
        for s in rev["sessions"]:
            if s.get("site") and s["totals"]["lights"]:
                out.append({"lat": s["site"][0], "lon": s["site"][1], "date": s["date"], "target": rev["catalogId"],
                            "revisionId": rev["id"], "session": s["folder"]})
    return out


def cluster_locations(locs: list[dict[str, Any]], radius_km: float = SITE_RADIUS_KM) -> list[dict[str, Any]]:
    """Greedy clustering of session locations; one cluster per place you have shot from."""
    clusters: list[dict[str, Any]] = []
    for loc in sorted(locs, key=lambda x: x["date"] or ""):
        for c in clusters:
            if distance_km(loc["lat"], loc["lon"], c["lat"], c["lon"]) <= radius_km:
                c["members"].append(loc)
                c["lat"] = statistics.median(m["lat"] for m in c["members"])
                c["lon"] = statistics.median(m["lon"] for m in c["members"])
                break
        else:
            clusters.append({"lat": loc["lat"], "lon": loc["lon"], "members": [loc]})
    out = []
    for c in clusters:
        dates = sorted({m["date"] for m in c["members"] if m["date"]})
        out.append({
            "lat": round(c["lat"], 5), "lon": round(c["lon"], 5),
            "nights": len(dates), "sessions": len(c["members"]),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "targets": sorted({m["target"] for m in c["members"]}),
        })
    return sorted(out, key=lambda c: -c["nights"])


def new_site_id() -> str:
    return uuid.uuid4().hex[:12]


def normalize_site(body: dict[str, Any]) -> dict[str, Any]:
    """Validated site record (raises ValueError with a readable message)."""
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("A site needs a name")
    lat, lon = parse_angle(body.get("lat")), parse_angle(body.get("lon"))
    if lat is None or not -90 <= lat <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if lon is None or not -180 <= lon <= 180:
        raise ValueError("Longitude must be between -180 and 180")
    elev = body.get("elevation")
    elev = None if elev in (None, "") else float(elev)
    sqm = float(body.get("sqm") or 18.0)
    if not 15 <= sqm <= 22.5:
        raise ValueError("SQM must be between 15 and 22.5 mag/arcsec²")
    min_alt = float(body.get("minAlt") if body.get("minAlt") is not None else 20)
    if not 0 <= min_alt <= 80:
        raise ValueError("Minimum altitude must be between 0° and 80°")
    horizon = []
    for p in body.get("horizon") or []:
        az, alt = float(p["az"]), float(p["alt"])
        if 0 <= az < 360 and 0 <= alt <= 90:
            horizon.append({"az": round(az, 1), "alt": round(alt, 1)})
    horizon.sort(key=lambda p: p["az"])
    return {
        "name": name[:80],
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "elevation": elev,
        "sqm": round(sqm, 2),
        "minAlt": round(min_alt, 1),
        "horizon": horizon,
        "isDefault": bool(body.get("isDefault")),
        "notes": str(body.get("notes") or "")[:1000],
    }


# ---------------------------------------------------------------------------- rig


def detect_rig(lights: list[Frame], days: int = 365) -> dict[str, Any] | None:
    """The most-used camera + telescope over the last year of lights, from the headers."""
    if not lights:
        return None
    latest = max((f.time_utc for f in lights if f.time_utc), default=None)
    cutoff = latest - timedelta(days=days) if latest else None
    recent = [f for f in lights if cutoff is None or (f.time_utc and f.time_utc >= cutoff)] or lights
    combos = Counter(
        (f.instrume, f.telescop, f.focallen, f.header.get("XPIXSZ"), f.header.get("NAXIS1"), f.header.get("NAXIS2"))
        for f in recent
    )
    (instrume, telescop, focallen, pix, w, h), n = combos.most_common(1)[0]
    filters = Counter(f.filter for f in recent if f.filter)
    exposures: dict[str, float] = {}
    for filt in filters:
        exp = Counter(f.exposure for f in recent if f.filter == filt and f.exposure)
        if exp:
            exposures[filt] = exp.most_common(1)[0][0]
    return {
        "instrume": instrume, "telescop": telescop, "focalLength": focallen, "pixelSizeUm": pix,
        "widthPx": w, "heightPx": h, "frames": n,
        "filters": [f for f, _ in filters.most_common()],
        "subSeconds": exposures,
    }


# ---------------------------------------------------------------------------- measured sky


def _load_center(path: Path, size: int = 512) -> np.ndarray:
    from .preview import _load

    data = _load(path)
    h, w = data.shape[:2]
    y0, x0 = max(0, h // 2 - size // 2), max(0, w // 2 - size // 2)
    return data[y0:y0 + size, x0:x0 + size]


def bias_pedestal(cache, library_frames: list[Frame], instrume: str | None, readout: str | None,
                  gain: float | None, offset: float | None, min_bias_exposure: float) -> dict[str, Any] | None:
    """Median level of raw bias frames at a light's settings, in the lights' ADU scale.

    Raw FITS biases are used (not PixInsight masters, which are normalised to 0–1).
    Biases shorter than the suspect threshold are skipped. Cached in the database.
    """
    key = f"pedestal:{instrume}|{readout}|{gain}|{offset}"
    cached = cache.get_json(key)
    if cached is not None:
        return cached or None
    candidates = [
        f for f in library_frames
        if f.kind == "BIAS" and f.status == "ok" and f.path.lower().endswith((".fits", ".fit", ".fts"))
        and f.instrume == instrume and f.readout == readout
        and (gain is None or f.gain == gain) and (offset is None or f.offset == offset)
        and (f.exposure or 0) >= min_bias_exposure
    ]
    candidates.sort(key=lambda f: f.time_utc or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    levels = []
    for f in candidates[:3]:
        try:
            levels.append(float(np.median(_load_center(Path(f.path)))))
        except Exception:  # noqa: BLE001 — an unreadable bias just isn't used
            continue
    result = {"adu": round(statistics.median(levels), 2), "frames": len(levels)} if levels else {}
    cache.set_json(key, result)
    return result or None


def sky_samples(cache, library, cfg, per_session: int = 12) -> dict[str, Any]:
    """Sky background in e⁻/s/px measured from analysed lights, by photon transfer.

    For a sky-limited sub the background variance in electrons equals the sky
    electrons themselves, so with S = background − bias pedestal and σ = the
    background noise (both in ADU) the sky collected is (S/σ)² electrons —
    independent of the camera gain, which is also reported (S/σ²) as a check.
    Read noise and PRNU are neglected: both are small next to a sky of hundreds of e⁻.
    """
    out = []
    pedestals: dict[tuple, dict | None] = {}
    sessions_used = 0
    for row in cache.diagnostic_summaries():
        stored = cache.diagnostic(row["revisionId"], row["session"])
        session = library.session(row["revisionId"], row["session"]) if stored else None
        rev = library.revisions.get(row["revisionId"])
        if not stored or not session or not rev or not session.get("site"):
            continue
        used = False
        for group in stored["result"].get("lights", []):
            kept = [fr for fr in group.get("frames", []) if not fr.get("rejected")]
            step = max(1, len(kept) // per_session)
            for fr in kept[::step][:per_session]:
                rel = fr.get("path")
                header = library.light_headers.get(rel) if rel else None
                bg, noise, exp = fr.get("background_median"), fr.get("noise_mad"), fr.get("exptime")
                if not header or not bg or not noise or not exp:
                    continue
                gain_ignored = cfg.gain_ignored(header.get("READOUTM"))
                pkey = (header.get("INSTRUME"), header.get("READOUTM"),
                        None if gain_ignored else header.get("GAIN"), None if gain_ignored else header.get("OFFSET"))
                if pkey not in pedestals:
                    pedestals[pkey] = bias_pedestal(cache, library.library_frames, *pkey, cfg.min_bias_exposure_s)
                ped = pedestals[pkey]
                if not ped:
                    continue
                signal = bg - ped["adu"]
                if signal <= 0:
                    continue
                t = parse_time(header.get("DATE-OBS")) or parse_time(fr.get("obs_time"))
                filt = library.cfg.filter_aliases.get(str(header.get("FILTER", "")).casefold(), header.get("FILTER"))
                out.append({
                    "revisionId": row["revisionId"],
                    "session": row["session"],
                    "filter": filt,
                    "time": t.replace(tzinfo=timezone.utc).isoformat() if t and t.tzinfo is None else (t.isoformat() if t else None),
                    "exposure": exp,
                    "ra": header.get("RA"), "dec": header.get("DEC"),
                    "lat": session["site"][0], "lon": session["site"][1],
                    "altitude": fr.get("altitude_deg"),
                    "skyE": round((signal / noise) ** 2, 2),
                    "skyRate": round((signal / noise) ** 2 / exp, 5),
                    "gainEPerAdu": round(signal / noise ** 2, 4),
                    "pixelSizeUm": header.get("XPIXSZ"), "focalLength": header.get("FOCALLEN"),
                })
                used = True
        sessions_used += used
    return {"sessions": sessions_used, "samples": out, "pedestals": {"|".join(map(str, k)): v for k, v in pedestals.items()}}
