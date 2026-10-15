"""Client of the Telescopius REST API (v2.2): object data, your target lists, sky events.

The key comes from the TELESCOPIUS_API_KEY environment variable and is added
here, server side, so it never reaches the browser and is never stored or
returned. Non-commercial use only (Telescopius terms). The API allows 60
requests a minute, and a target search counts as two, so calls are spaced
(MIN_INTERVAL_S) and the engine caches every answer (api_telescopius).

Verified on real responses (2026-10-14):
- search results come under `page_results`, not `objects` as the spec says;
- `ra` is in hours, `major_axis`/`minor_axis` in arcseconds, list `size_deg` in degrees;
- event `datetime` is ISO 8601 UTC.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

KEY_ENV = "TELESCOPIUS_API_KEY"
BASE = "https://api.telescopius.com/v2.2"
# 60 requests / 60 s, a search costs 2: one call every 2.1 s never hits the limit.
MIN_INTERVAL_S = 2.1

_lock = threading.Lock()
_last_call = 0.0


class TelescopiusError(RuntimeError):
    """A failure worth showing as-is on a page."""


def key_set() -> bool:
    return bool(os.environ.get(KEY_ENV))


def _get(path: str, params: dict[str, Any] | None = None, timeout_s: float = 20.0) -> Any:
    global _last_call
    key = os.environ.get(KEY_ENV)
    if not key:
        raise TelescopiusError(f"Set {KEY_ENV} in .env at the project root (or the engine's environment) and restart the engine to use Telescopius.")
    with _lock:
        wait = _last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = httpx.get(f"{BASE}{path}", params=params, timeout=timeout_s,
                          headers={"Authorization": f"Key {key}", "User-Agent": "AstroDashboard"})
        except httpx.HTTPError as exc:
            raise TelescopiusError(f"Telescopius is not reachable ({type(exc).__name__}).") from exc
        finally:
            _last_call = time.monotonic()
    if r.status_code in (401, 403):
        raise TelescopiusError(f"Telescopius refused the key (check {KEY_ENV}; the API is for patrons and sponsors).")
    if r.status_code == 429:
        raise TelescopiusError("Telescopius is rate limiting this key; try again in a minute.")
    if r.status_code != 200:
        raise TelescopiusError(f"Telescopius error {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise TelescopiusError("Telescopius answered with something that is not JSON.") from exc


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _arcmin(arcsec: Any) -> float | None:
    v = _num(arcsec)
    return round(v / 60, 2) if v else None


def _squash(s: str) -> str:
    return "".join(s.split()).casefold()


def normalize_object(o: dict[str, Any]) -> dict[str, Any]:
    """One Telescopius object in our units: RA/Dec in degrees, sizes in arcminutes."""
    ra_h = _num(o.get("ra"))
    return {
        "id": o.get("main_id"),
        "name": o.get("main_name") or o.get("main_id"),
        "ids": list(dict.fromkeys([*(o.get("ids") or []), *(o.get("alt_ids") or [])])),
        "names": o.get("names") or [],
        "types": o.get("types") or [],
        "ra": round(ra_h * 15, 6) if ra_h is not None else None,
        "dec": _num(o.get("dec")),
        "majArcmin": _arcmin(o.get("major_axis")),
        "minArcmin": _arcmin(o.get("minor_axis")),
        "paDeg": _num(o.get("pa_east")),
        "vmag": _num(o.get("visual_mag")),
        "photoMag": _num(o.get("photo_mag")),
        "surfBr": _num(o.get("subr")),
        "con": o.get("con"),
        "conName": o.get("con_name"),
        "morph": o.get("morph_class"),
        "url": o.get("url"),
        "imageUrl": o.get("main_image_url"),
        "thumbUrl": o.get("thumbnail_url"),
    }


def lookup(name: str) -> dict[str, Any] | None:
    """The object whose main ID, any catalogue ID or common name is `name` (spaces and case ignored); None if unknown."""
    data = _get("/targets/search", {"name": name, "name_exact": 1, "results_per_page": 5})
    rows = [r.get("object") or {} for r in (data.get("page_results") or data.get("objects") or [])]
    want = _squash(name)
    for o in rows:
        labels = [o.get("main_id"), o.get("main_name"), *(o.get("ids") or []), *(o.get("alt_ids") or []), *(o.get("names") or [])]
        if any(isinstance(x, str) and _squash(x) == want for x in labels):
            return normalize_object(o)
    return normalize_object(rows[0]) if len(rows) == 1 else None


def normalize_list_target(t: dict[str, Any]) -> dict[str, Any]:
    ra_h = _num(t.get("ra_hr"))
    size = _num(t.get("size_deg"))
    return {
        "id": t.get("object_id"),
        "name": t.get("name") or t.get("object_id"),
        "ra": round(ra_h * 15, 6) if ra_h is not None else None,
        "dec": _num(t.get("dec")),
        "sizeArcmin": round(size * 60, 2) if size else None,
        "paDeg": _num(t.get("position_angle_east")),
        "vmag": _num(t.get("visual_mag")),
        "con": t.get("constellation_id"),
        "thumbUrl": t.get("thumbnail_url"),
        "notes": t.get("notes"),
        "fov": ({"width": t["fov_width_deg"], "height": t.get("fov_height_deg")} if t.get("fov_width_deg") else None),
        "panel": t.get("panel_number"),
    }


def target_lists() -> list[dict[str, Any]]:
    """Every target list of the key's user, with its targets."""
    out = []
    for summary in _get("/target-lists").get("lists") or []:
        full = _get(f"/target-lists/{summary['id']}")
        out.append({
            "id": summary["id"],
            "name": summary.get("name") or summary["id"],
            "url": summary.get("url"),
            "targets": [normalize_list_target(t) for t in full.get("targets") or [] if t.get("ra_hr") is not None],
        })
    return out


def normalize_event(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": e.get("type"),
        "name": e.get("name"),
        "t": e.get("datetime"),
        "objects": e.get("objects") or [],
        "deg": _num(e.get("deg")),
        "visibleLocally": e.get("visible_locally"),
        "regions": e.get("geographic_regions"),
        "rating": e.get("rating"),
        "zhr": _num(e.get("zhr")),
        "moonIllum": _num(e.get("moon_illumination")),
        "activeWindow": e.get("active_window"),
        "parent": e.get("parent_body"),
    }


def events(lat: float, lon: float, limit: int = 50) -> list[dict[str, Any]]:
    """Notable sky events over the next 30 days for a location, in time order."""
    data = _get("/events/highlights", {"lat": round(lat, 4), "lon": round(lon, 4), "limit": limit})
    return sorted((normalize_event(e) for e in data.get("events") or [] if e.get("datetime")), key=lambda e: e["t"])
