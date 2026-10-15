"""Telescopius endpoints: object data, your target lists, sky events — all cached.

Object data barely changes, so a found object is kept for good and an unknown
name is retried after a month. Lists and events are refreshed every 6 hours.
A failed call is not retried for 10 minutes (pages keep loading without it),
and the last good answer is served, marked stale, meanwhile.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query

from . import telescopius

log = logging.getLogger(__name__)

OBJECT_MISS_TTL_S = 30 * 86400
LISTS_TTL_S = 6 * 3600
EVENTS_TTL_S = 6 * 3600
RETRY_S = 600


def object_key(name: str) -> str:
    return "telescopius-obj-v1:" + "".join(name.split()).casefold()


def register(app: FastAPI, state) -> None:
    cache = state.cache
    failures: dict[str, tuple[float, str]] = {}

    def _no_key() -> dict[str, Any]:
        return {"keySet": False, "error": f"Set {telescopius.KEY_ENV} in .env at the project root and restart the engine to use Telescopius."}

    def _cached(key: str, ttl_s: float, fetch: Callable[[], Any], refresh: bool = False, keep_hits: bool = False) -> dict[str, Any]:
        """{data, fetchedAt, stale, error} for one cache key, fetching when older than ttl_s (never, for a hit, with keep_hits)."""
        cached = cache.get_json(key)
        if cached and not refresh and ((keep_hits and cached["data"]) or time.time() - cached["fetchedAt"] < ttl_s):
            return {**cached, "stale": False, "error": None}
        failed = failures.get(key)
        try:
            if failed and not refresh and time.time() - failed[0] < RETRY_S:
                raise telescopius.TelescopiusError(failed[1])
            fresh = {"fetchedAt": time.time(), "data": fetch()}
        except telescopius.TelescopiusError as exc:
            if not failed or failed[1] != str(exc):
                log.warning("telescopius %s failed: %s", key, exc)
            if not failed or time.time() - failed[0] >= RETRY_S:
                failures[key] = (time.time(), str(exc))
            return {"fetchedAt": cached["fetchedAt"] if cached else None, "data": cached["data"] if cached else None,
                    "stale": bool(cached), "error": str(exc)}
        failures.pop(key, None)
        cache.set_json(key, fresh)
        return {**fresh, "stale": False, "error": None}

    @app.get("/api/telescopius/status")
    def status() -> dict[str, Any]:
        return {"keySet": telescopius.key_set()}

    @app.get("/api/telescopius/object")
    def object_info(name: list[str] = Query(default=[]), refresh: bool = False) -> dict[str, Any]:  # noqa: B008
        """The first of the given names Telescopius knows (e.g. ?name=Sh2-129&name=Flying Bat), or object: null."""
        names = [n.strip() for n in name if n and n.strip()][:4]
        if not names:
            raise HTTPException(422, detail="Give at least one name")
        if not telescopius.key_set():
            return {**_no_key(), "object": None}
        error = None
        for n in names:
            got = _cached(object_key(n), OBJECT_MISS_TTL_S, lambda n=n: telescopius.lookup(n), refresh, keep_hits=True)
            if got["data"]:
                return {"keySet": True, "object": got["data"], "matchedName": n, "error": None}
            error = error or got["error"]
        return {"keySet": True, "object": None, "matchedName": None, "error": error}

    @app.get("/api/telescopius/lists")
    def lists(refresh: bool = False) -> dict[str, Any]:
        if not telescopius.key_set():
            return {**_no_key(), "lists": []}
        got = _cached("telescopius-lists-v1", LISTS_TTL_S, telescopius.target_lists, refresh)
        return {"keySet": True, "lists": got["data"] or [], "fetchedAt": got["fetchedAt"], "stale": got["stale"], "error": got["error"]}

    @app.get("/api/telescopius/events")
    def events(siteId: str | None = None, refresh: bool = False) -> dict[str, Any]:  # noqa: N803
        sites = cache.sites()
        site = (next((s for s in sites if s["id"] == siteId), None)
                or next((s for s in sites if s.get("isDefault")), None) or (sites[0] if sites else None))
        if site is None:
            raise HTTPException(404, detail="No such site")
        if not telescopius.key_set():
            return {**_no_key(), "siteId": site["id"], "events": []}
        key = f"telescopius-events-v2:{site['lat']:.2f}|{site['lon']:.2f}"
        got = _cached(key, EVENTS_TTL_S, lambda: telescopius.events(site["lat"], site["lon"]), refresh)
        return {"keySet": True, "siteId": site["id"], "events": got["data"] or [], "fetchedAt": got["fetchedAt"],
                "stale": got["stale"], "error": got["error"]}
