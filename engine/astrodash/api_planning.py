"""Planning endpoints: sites, goals, settings, planning context, measured sky and the Hα anchor.

Everything is stored in the dashboard database (var/). Goals live there too
rather than in each revision's target.json, because writing into the data root
is reserved for approved organizer plans (PLAN.md §6.4, Phase 6 exports them).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import halpha, planning

log = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    # Implied hours above this flag an SNR goal as unrealistic (PLAN §6.4).
    "goalCapHours": 40,
    # Rig used by the SNR model; null fields are filled from the headers in the web app.
    "rig": None,
    # AstroController, for Live (its token comes from ASTROCONTROLLER_TOKEN, never from here).
    "liveUrl": "http://127.0.0.1:3005",
}


class GoalBody(BaseModel):
    mode: Literal["hours", "snr", "none"]
    hours: float | None = Field(default=None, ge=0, le=1000)
    snr: float | None = Field(default=None, ge=0, le=10000)


class GoalsBody(BaseModel):
    goals: dict[str, GoalBody]


class HalphaItem(BaseModel):
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    maj: float | None = Field(default=None, ge=0)
    min: float | None = Field(default=None, ge=0)
    pa: float | None = None


class HalphaLookup(BaseModel):
    items: list[HalphaItem] = Field(max_length=2000)
    # Queue the ones not measured yet for a polite background fetch (1 request / s).
    prefetch: bool = True


def halpha_key(ra: float, dec: float, maj: float | None, mnr: float | None, pa: float | None) -> str:
    return f"halpha:{ra:.4f}|{dec:.4f}|{maj or 0:.2f}|{mnr or 0:.2f}|{pa or 0:.1f}"


class HalphaPrefetcher:
    """One background thread fetching survey cutouts one at a time; results land in the cache."""

    def __init__(self, cache):
        self.cache = cache
        self.queue: list[HalphaItem] = []
        self.failed: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def add(self, items: list[HalphaItem]) -> int:
        with self._lock:
            queued = {halpha_key(i.ra, i.dec, i.maj, i.min, i.pa) for i in self.queue}
            new = [i for i in items if (k := halpha_key(i.ra, i.dec, i.maj, i.min, i.pa)) not in queued and k not in self.failed]
            self.queue.extend(new)
            if new and not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="halpha-prefetch", daemon=True)
                self._thread.start()
            return len(self.queue)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self.queue:
                    return
                item = self.queue.pop(0)
            key = halpha_key(item.ra, item.dec, item.maj, item.min, item.pa)
            if self.cache.get_json(key):
                continue
            try:
                self.cache.set_json(key, halpha.measure(item.ra, item.dec, item.maj, item.min, item.pa))
            except Exception as exc:  # noqa: BLE001 — offline: stop quietly, retried on a later page load
                log.info("halpha prefetch stopped: %s", exc)
                with self._lock:
                    self.failed.add(key)
                    self.queue.clear()
                return
            time.sleep(1.0)


def register(app: FastAPI, state) -> None:
    cfg = state.cfg
    cache = state.cache
    prefetcher = HalphaPrefetcher(cache)

    # ------------------------------------------------------------------ sites

    def _sites() -> list[dict[str, Any]]:
        sites = cache.sites()
        if not sites and state.library is not None:
            # First use: pre-fill Home from where most nights were shot (PLAN §6.1).
            clusters = planning.cluster_locations(planning.session_locations(state.library))
            if clusters:
                home = clusters[0]
                cache.save_site(planning.new_site_id(), planning.normalize_site({
                    "name": "Home", "lat": home["lat"], "lon": home["lon"], "sqm": 18.0, "minAlt": 20,
                    "isDefault": True, "notes": "Pre-filled from the FITS headers; SQM 18 is a starting value.",
                }))
                sites = cache.sites()
        if sites and not any(s.get("isDefault") for s in sites):
            sites[0]["isDefault"] = True
        return sites

    def _with_usage(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if state.library is None:
            return sites
        usage: dict[str, list[str]] = defaultdict(list)
        for loc in planning.session_locations(state.library):
            s = planning.nearest_site(sites, loc["lat"], loc["lon"])
            if s and loc["date"]:
                usage[s["id"]].append(loc["date"])
        for s in sites:
            dates = sorted(set(usage.get(s["id"], [])))
            s["nights"] = len(dates)
            s["lastNight"] = dates[-1] if dates else None
        return sites

    def _save(site_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            record = planning.normalize_site(body)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        if record["isDefault"]:
            for other in cache.sites():
                if other["id"] != site_id and other.get("isDefault"):
                    cache.save_site(other["id"], {**other, "isDefault": False})
        cache.save_site(site_id, record)
        return next(s for s in cache.sites() if s["id"] == site_id)

    @app.get("/api/sites")
    def sites() -> dict[str, Any]:
        return {"sites": _with_usage(_sites())}

    @app.post("/api/sites")
    def create_site(body: dict[str, Any]) -> dict[str, Any]:
        return {"site": _save(planning.new_site_id(), body)}

    @app.put("/api/sites/{site_id}")
    def update_site(site_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not any(s["id"] == site_id for s in cache.sites()):
            raise HTTPException(404, detail="No such site")
        return {"site": _save(site_id, body)}

    @app.delete("/api/sites/{site_id}")
    def delete_site(site_id: str) -> dict[str, Any]:
        current = cache.sites()
        if not any(s["id"] == site_id for s in current):
            raise HTTPException(404, detail="No such site")
        if len(current) <= 1:
            raise HTTPException(409, detail="At least one site must exist")
        cache.delete_site(site_id)
        return {"ok": True}

    @app.post("/api/sites/import")
    def import_sites(body: dict[str, Any]) -> dict[str, Any]:
        items = body.get("sites")
        if not isinstance(items, list) or not items or len(items) > 200:
            raise HTTPException(422, detail="Expected {\"sites\": [...]} with 1–200 sites")
        try:
            records = [planning.normalize_site(s) for s in items]
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        existing = cache.sites()
        added, updated = 0, 0
        for rec in records:
            same = planning.nearest_site(existing, rec["lat"], rec["lon"], 0.05)
            if same:
                cache.save_site(same["id"], {**rec, "isDefault": same.get("isDefault", False)})
                updated += 1
            else:
                cache.save_site(planning.new_site_id(), {**rec, "isDefault": False})
                added += 1
        return {"added": added, "updated": updated}

    @app.get("/api/sites/suggest")
    def suggest_sites() -> dict[str, Any]:
        lib = state.require_library()
        known = _sites()
        out = []
        for c in planning.cluster_locations(planning.session_locations(lib)):
            near = planning.nearest_site(known, c["lat"], c["lon"])
            out.append({**c, "knownSite": near["id"] if near else None, "knownSiteName": near["name"] if near else None})
        return {"suggestions": out}

    @app.get("/api/sites/from-session")
    def site_from_session(revisionId: str, session: str) -> dict[str, Any]:  # noqa: N803
        lib = state.require_library()
        sess = lib.session(revisionId, session)
        if sess is None:
            raise HTTPException(404, detail="No such session")
        prefix = None
        for rel, header in lib.light_headers.items():
            if f"/{session}/" in f"/{rel}" and rel.startswith(lib.revisions[revisionId]["folder"] + "/"):
                prefix = header
                break
        if prefix is None:
            raise HTTPException(404, detail="No light header found for that session")
        return {"site": planning.site_from_header(prefix)}

    # ------------------------------------------------------------------ settings

    def _settings() -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **(cache.get_json("settings") or {})}

    @app.get("/api/settings")
    def settings() -> dict[str, Any]:
        lib = state.library
        return {"settings": _settings(), "detectedRig": planning.detect_rig(lib.ok_lights) if lib else None}

    @app.put("/api/settings")
    def update_settings(body: dict[str, Any]) -> dict[str, Any]:
        unknown = set(body) - set(DEFAULT_SETTINGS)
        if unknown:
            raise HTTPException(422, detail=f"Unknown settings: {', '.join(sorted(unknown))}")
        if len(json.dumps(body)) > 50_000:
            raise HTTPException(422, detail="Settings payload too large")
        if "goalCapHours" in body:
            cap = body["goalCapHours"]
            if not isinstance(cap, (int, float)) or not 1 <= cap <= 500:
                raise HTTPException(422, detail="goalCapHours must be between 1 and 500")
        if "liveUrl" in body and not (isinstance(body["liveUrl"], str) and body["liveUrl"].startswith(("http://", "https://"))):
            raise HTTPException(422, detail="liveUrl must be an http(s) URL")
        merged = {**(cache.get_json("settings") or {}), **body}
        cache.set_json("settings", merged)
        return {"settings": _settings()}

    # ------------------------------------------------------------------ goals

    @app.get("/api/goals")
    def goals() -> dict[str, Any]:
        return {"goals": cache.goals()}

    @app.put("/api/goals/{rev_id}")
    def save_goals(rev_id: str, body: GoalsBody) -> dict[str, Any]:
        lib = state.require_library()
        if rev_id not in lib.revisions:
            raise HTTPException(404, detail="No such target revision")
        clean = {}
        for filt, g in body.goals.items():
            if g.mode == "hours" and g.hours is None:
                raise HTTPException(422, detail=f"{filt}: an hours goal needs hours")
            if g.mode == "snr" and g.snr is None:
                raise HTTPException(422, detail=f"{filt}: an SNR goal needs a target SNR")
            clean[filt[:20]] = {"mode": g.mode, **({"hours": g.hours} if g.hours is not None else {}),
                                **({"snr": g.snr} if g.snr is not None else {})}
        cache.save_goals(rev_id, clean)
        return {"goals": clean}

    # ------------------------------------------------------------------ planning context

    def _good_seconds(rev: dict[str, Any]) -> dict[str, dict[str, float]]:
        """Per filter: all ok seconds, and 'good' seconds where diagnostics kept/rejected frames."""
        stored = {(r["revisionId"], r["session"]): r for r in cache.diagnostic_summaries() if r["revisionId"] == rev["id"]}
        out: dict[str, dict[str, float]] = defaultdict(lambda: {"seconds": 0.0, "goodSeconds": 0.0, "analysedSeconds": 0.0})
        for s in rev["sessions"]:
            raw: dict[str, float] = defaultdict(float)
            for g in s["lightGroups"]:
                if g["frames"]:
                    raw[g["filter"]] += g["seconds"]
            diag = stored.get((rev["id"], s["folder"]))
            kept: dict[str, float] = defaultdict(float)
            if diag:
                for g in diag["summary"].get("lights", []):
                    filt = cfg.filter_aliases.get(str(g.get("filter", "")).casefold(), g.get("filter"))
                    kept[filt] += g.get("keptSeconds") or 0
            for filt, secs in raw.items():
                o = out[filt]
                o["seconds"] += secs
                if diag and filt in kept:
                    o["goodSeconds"] += min(secs, kept[filt])
                    o["analysedSeconds"] += secs
                else:
                    o["goodSeconds"] += secs
        return dict(out)

    @app.get("/api/planning/context")
    def planning_context() -> dict[str, Any]:
        lib = state.require_library()
        stored_goals = cache.goals()
        targets = []
        for rev in lib.revisions.values():
            per_filter = _good_seconds(rev)
            targets.append({
                "id": rev["id"], "folder": rev["folder"], "catalogId": rev["catalogId"], "name": rev["name"],
                "revision": rev["revision"], "groupId": rev["groupId"], "stage": rev["stage"]["value"],
                "pointing": rev["pointing"], "panels": rev["panels"],
                "lastNight": rev["totals"]["last"], "nights": rev["totals"]["nights"],
                "filters": [{"filter": f, **v} for f, v in per_filter.items()],
                "goals": stored_goals.get(rev["id"]) or rev.get("goals") or {},
                "goalsSource": "dashboard" if rev["id"] in stored_goals else ("target.json" if rev.get("goals") else None),
            })
        return {
            "targets": sorted(targets, key=lambda t: t["catalogId"].casefold()),
            "sites": _with_usage(_sites()),
            "settings": _settings(),
            "detectedRig": planning.detect_rig(lib.ok_lights),
        }

    @app.get("/api/planning/sky")
    def measured_sky() -> dict[str, Any]:
        lib = state.require_library()
        return planning.sky_samples(cache, lib, cfg)

    @app.get("/api/planning/halpha")
    def halpha_anchor(ra: float, dec: float, maj: float | None = None, min: float | None = None,  # noqa: A002
                      pa: float | None = None, refresh: bool = False, cachedOnly: bool = False) -> dict[str, Any]:  # noqa: N803
        key = halpha_key(ra, dec, maj, min, pa)
        if not refresh:
            cached = cache.get_json(key)
            if cached:
                return {**cached, "cached": True}
        if cachedOnly:
            raise HTTPException(404, detail="Not measured yet")
        try:
            result = halpha.measure(ra, dec, maj, min, pa)
        except Exception as exc:  # noqa: BLE001 — offline, or the service refused
            log.warning("halpha fetch failed: %s", exc)
            raise HTTPException(502, detail=f"The Hα survey could not be fetched ({type(exc).__name__}). "
                                            "The planner falls back to a typical value for the object class.") from exc
        cache.set_json(key, result)
        return {**result, "cached": False}

    @app.post("/api/planning/halpha/lookup")
    def halpha_lookup(body: HalphaLookup) -> dict[str, Any]:
        """Cached survey values for many targets at once; the missing ones are fetched in the background."""
        found: dict[str, Any] = {}
        missing: list[HalphaItem] = []
        for item in body.items:
            hit = cache.get_json(halpha_key(item.ra, item.dec, item.maj, item.min, item.pa))
            if hit:
                found[f"{item.ra:.3f}|{item.dec:.3f}"] = hit
            else:
                missing.append(item)
        queued = prefetcher.add(missing) if body.prefetch and missing else len(prefetcher.queue)
        return {"found": found, "missing": len(missing), "queued": queued}
