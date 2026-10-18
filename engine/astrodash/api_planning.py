"""Planning endpoints: sites, goals, settings, planning context, measured sky and the Hα anchor.

Everything is stored in the dashboard database (var/). Goals live there too
rather than in each revision's target.json, because writing into the data root
is reserved for approved organizer plans (PLAN.md §6.4, Phase 6 exports them).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import astrospheric, halpha, planning
from .observatory.llm import EFFORTS, LlmError, split_model
from .observatory.guard import LIMITS as GUARD_LIMITS
from .observatory.runtime import DEFAULTS as OBS_DEFAULTS

log = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict[str, Any] = {
    # Implied hours above this flag an SNR goal as unrealistic (PLAN §6.4).
    "goalCapHours": 40,
    # Rig used by the SNR model; null fields are filled from the headers in the web app.
    "rig": None,
    # Live: where NINA (Advanced API plugin) and PHD2 (event server) run.
    "observatory": dict(OBS_DEFAULTS["observatory"]),
    # Live → Guiding: the AI advisor (suggests guiding changes; its API key comes from .env, never from here).
    "advisor": dict(OBS_DEFAULTS["advisor"]),
    # Live: stop the NINA sequence while the guide star is lost, resume it once guiding is steady (off by default).
    "guard": dict(OBS_DEFAULTS["guard"]),
}
# Settings that are no longer used (AstroController's address); dropped on the next save.
RETIRED_SETTINGS = {"liveUrl"}

# Astrospheric publishes a new model run every 6 h; an hour keeps the page fresh without spending credits.
FORECAST_TTL_S = 3600
FORECAST_RETRY_S = 600


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
    forecast_failures: dict[str, tuple[float, str]] = {}  # cache key → (when, message): no retry on every page load

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
        stored = {k: v for k, v in (cache.get_json("settings") or {}).items() if k not in RETIRED_SETTINGS}
        out = {**DEFAULT_SETTINGS, **stored}
        for key in ("observatory", "advisor", "guard"):  # partial objects keep the defaults for missing fields
            out[key] = {**DEFAULT_SETTINGS[key], **(stored.get(key) or {})}
        return out

    def _check_observatory(o: Any) -> dict[str, Any]:
        if not isinstance(o, dict) or set(o) - set(DEFAULT_SETTINGS["observatory"]):
            raise HTTPException(422, detail="observatory: unknown fields")
        for key in ("ninaHost", "phd2Host"):
            if key in o and not (isinstance(o[key], str) and 0 < len(o[key].strip()) <= 253 and " " not in o[key].strip()):
                raise HTTPException(422, detail=f"observatory.{key} must be a host name or IP address")
        if "ninaPort" in o and not (isinstance(o["ninaPort"], int) and 1 <= o["ninaPort"] <= 65535):
            raise HTTPException(422, detail="observatory.ninaPort must be a port number")
        if "phd2Instance" in o and not (isinstance(o["phd2Instance"], int) and 1 <= o["phd2Instance"] <= 9):
            raise HTTPException(422, detail="observatory.phd2Instance must be 1–9")
        return {k: v.strip() if isinstance(v, str) else v for k, v in o.items()}

    def _check_advisor(a: Any) -> dict[str, Any]:
        if not isinstance(a, dict) or set(a) - set(DEFAULT_SETTINGS["advisor"]):
            raise HTTPException(422, detail="advisor: unknown fields")
        if "enabled" in a and not isinstance(a["enabled"], bool):
            raise HTTPException(422, detail="advisor.enabled must be true or false")
        if "model" in a:
            try:
                split_model(a["model"])
            except (LlmError, AttributeError) as exc:
                raise HTTPException(422, detail=f"advisor.model: {exc}") from exc
        if "ollamaUrl" in a and not (isinstance(a["ollamaUrl"], str) and a["ollamaUrl"].startswith(("http://", "https://"))):
            raise HTTPException(422, detail="advisor.ollamaUrl must be an http(s) URL")
        if "autoApply" in a and not isinstance(a["autoApply"], bool):
            raise HTTPException(422, detail="advisor.autoApply must be true or false")
        if "effort" in a and a["effort"] not in EFFORTS:
            raise HTTPException(422, detail=f"advisor.effort must be one of {', '.join(EFFORTS)}")
        if "intervalMin" in a and not (isinstance(a["intervalMin"], (int, float)) and 2 <= a["intervalMin"] <= 120):
            raise HTTPException(422, detail="advisor.intervalMin must be 2–120 minutes")
        if "targetRms" in a and not (isinstance(a["targetRms"], (int, float)) and 0.2 <= a["targetRms"] <= 5):
            raise HTTPException(422, detail="advisor.targetRms must be 0.2–5 arcsec")
        return a

    def _check_guard(g: Any) -> dict[str, Any]:
        if not isinstance(g, dict) or set(g) - set(DEFAULT_SETTINGS["guard"]):
            raise HTTPException(422, detail="guard: unknown fields")
        if "enabled" in g and not isinstance(g["enabled"], bool):
            raise HTTPException(422, detail="guard.enabled must be true or false")
        for key, (lo, hi) in GUARD_LIMITS.items():
            if key in g and not (isinstance(g[key], (int, float)) and not isinstance(g[key], bool) and lo <= g[key] <= hi):
                raise HTTPException(422, detail=f"guard.{key} must be {lo}–{hi}")
        return g

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
        stored = {k: v for k, v in (cache.get_json("settings") or {}).items() if k not in RETIRED_SETTINGS}
        if "observatory" in body:
            body["observatory"] = {**(stored.get("observatory") or {}), **_check_observatory(body["observatory"])}
        if "advisor" in body:
            body["advisor"] = {**(stored.get("advisor") or {}), **_check_advisor(body["advisor"])}
        if "guard" in body:
            body["guard"] = {**(stored.get("guard") or {}), **_check_guard(body["guard"])}
        reconnect = "observatory" in body and body["observatory"] != (stored.get("observatory") or {})
        cache.set_json("settings", {**stored, **body})
        obs, loop = getattr(state, "observatory", None), getattr(state, "loop", None)
        if reconnect and obs is not None and loop is not None and obs.tasks:
            asyncio.run_coroutine_threadsafe(obs.reconfigure(), loop)
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

    @app.get("/api/planning/context")
    def planning_context() -> dict[str, Any]:
        lib = state.require_library()
        stored_goals = cache.goals()
        summaries = cache.diagnostic_summaries()
        targets = []
        for rev in lib.revisions.values():
            per_filter, _ = planning.accepted_seconds(rev, summaries, cfg.filter_aliases)
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

    @app.get("/api/planning/forecast")
    def forecast(siteId: str | None = None, refresh: bool = False) -> dict[str, Any]:  # noqa: N803
        """Astrospheric's hourly forecast for a site, cached per location (key from ASTROSPHERIC_API_KEY)."""
        sites = _sites()
        site = next((s for s in sites if s["id"] == siteId), None) or next((s for s in sites if s.get("isDefault")), None)
        if site is None:
            raise HTTPException(404, detail="No such site")
        if not astrospheric.key_set():
            return {"keySet": False, "siteId": site["id"], "forecast": None,
                    "error": f"Set {astrospheric.KEY_ENV} in .env at the project root (or the engine's environment) and restart the engine to get Astrospheric forecasts."}
        key = f"astrospheric-v5:{site['lat']:.3f}|{site['lon']:.3f}"
        cached = cache.get_json(key)
        if cached and not refresh and time.time() - cached["fetchedAt"] < FORECAST_TTL_S:
            return {"keySet": True, "siteId": site["id"], "cached": True, **cached}
        failed = forecast_failures.get(key)
        try:
            if failed and not refresh and time.time() - failed[0] < FORECAST_RETRY_S:
                raise astrospheric.ForecastError(failed[1])
            fresh = {"fetchedAt": time.time(), "forecast": astrospheric.fetch(site["lat"], site["lon"]), "error": None}
        except astrospheric.ForecastError as exc:
            if not failed or failed[1] != str(exc) or time.time() - failed[0] >= FORECAST_RETRY_S:
                log.warning("astrospheric forecast failed: %s", exc)
                forecast_failures[key] = (time.time(), str(exc))
            # Keep showing the last forecast we have (marked stale) rather than nothing.
            return {"keySet": True, "siteId": site["id"], "cached": True, "stale": bool(cached),
                    "fetchedAt": cached["fetchedAt"] if cached else None,
                    "forecast": cached["forecast"] if cached else None, "error": str(exc)}
        forecast_failures.pop(key, None)
        cache.set_json(key, fresh)
        return {"keySet": True, "siteId": site["id"], "cached": False, **fresh}

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
