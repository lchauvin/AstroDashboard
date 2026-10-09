"""Live (AstroController proxy) and auto-ingest endpoints."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .ingest.planner import NIGHT_DIR_RE
from .ingest.service import IngestService
from .live import AstroControllerClient


class ModeBody(BaseModel):
    mode: Literal["off", "dry-run", "auto"]


class NightBody(BaseModel):
    night: str = Field(min_length=10, max_length=20)


class CleanupBody(NightBody):
    # Removing folders is a deletion (even empty ones): the UI asks, and the request must say so.
    confirm: bool = False


class MapBody(BaseModel):
    object: str = Field(min_length=1, max_length=200)
    revisionId: str | None = Field(default=None, max_length=300)  # noqa: N815
    catalogId: str | None = Field(default=None, max_length=200)  # noqa: N815


def register(app: FastAPI, state) -> None:
    cache = state.cache
    live = AstroControllerClient(lambda: (cache.get_json("settings") or {}).get("liveUrl"))
    state.live = live

    def on_moved() -> None:
        state.start_scan()  # pick up the new nights in the Library

    def sequence_running() -> bool | None:
        live.refresh_if_stale()
        return live.sequence_running()

    service = IngestService(state.cfg, cache, lambda: state.library, on_moved, sequence_running)
    state.ingest = service

    def _night(n: str) -> str:
        if not NIGHT_DIR_RE.match(n) or os.sep in n or "/" in n:
            raise HTTPException(422, detail="Not a .Download night folder name")
        return n

    # ------------------------------------------------------------------ live

    @app.get("/api/live/state")
    def live_state() -> dict[str, Any]:
        return live.state()

    @app.get("/api/live/frame.png")
    def live_frame(v: str | None = None):  # noqa: ARG001 — cache buster
        got = live.image("/api/frame/latest.png")
        if not got:
            raise HTTPException(404, detail="No frame preview")
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "no-store"})

    @app.get("/api/live/guide-star.png")
    def live_guide(v: str | None = None):  # noqa: ARG001
        got = live.image("/api/guide-star.png")
        if not got:
            raise HTTPException(404, detail="No guide star image")
        return Response(got[0], media_type=got[1], headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ ingest

    @app.get("/api/ingest")
    def ingest_status() -> dict[str, Any]:
        st = service.status()
        journal = cache.journal(limit=5000)
        nights: dict[str, dict[str, Any]] = defaultdict(lambda: {"moved": 0, "copied": 0, "undone": 0, "errors": 0, "targets": {}, "last": 0})
        for r in journal:
            n = nights[r["downloadNight"]]
            n["last"] = max(n["last"], r["ts"])
            if r["error"]:
                n["errors"] += 1
            elif r["undone"]:
                n["undone"] += 1
            else:
                n["moved" if r["op"] == "move" else "copied"] += 1
                key = f'{r["revision"]}|{r["kind"]}|{r["filter"]}'
                n["targets"][key] = n["targets"].get(key, 0) + 1
        for name in list(nights):
            nights[name]["emptyDirs"] = len(service.empty_dirs(name))
        if st["plan"]:
            for name in st["plan"]["nights"]:
                st["plan"]["nights"][name]["emptyDirs"] = len(service.empty_dirs(name))
        lib = state.library
        return {
            **st,
            "journal": {k: dict(v) for k, v in sorted(nights.items(), reverse=True)},
            "recent": journal[:200],
            "objectMap": sorted(service.object_map(lib).values(), key=lambda m: m["object"].casefold()) if lib else [],
            "targets": [
                {"id": r["id"], "folder": r["folder"], "catalogId": r["catalogId"], "stage": r["stage"]["value"], "revision": r["revision"]}
                for r in sorted(lib.revisions.values(), key=lambda r: r["folder"].casefold())
            ] if lib else [],
        }

    @app.post("/api/ingest/mode")
    def ingest_mode(body: ModeBody) -> dict[str, Any]:
        service.set_mode(body.mode)
        return {"mode": service.mode}

    @app.post("/api/ingest/run")
    def ingest_run() -> dict[str, Any]:
        state.require_library()
        return service.run_once()

    @app.post("/api/ingest/close")
    def ingest_close(body: NightBody) -> dict[str, Any]:
        service.close_night(_night(body.night))
        return {"ok": True}

    @app.post("/api/ingest/undo")
    def ingest_undo(body: NightBody) -> dict[str, Any]:
        state.require_library()
        return service.undo_night(_night(body.night))

    @app.post("/api/ingest/resume")
    def ingest_resume(body: NightBody) -> dict[str, Any]:
        service.resume_night(_night(body.night))
        return {"ok": True}

    @app.post("/api/ingest/cleanup")
    def ingest_cleanup(body: CleanupBody) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(422, detail="Removing empty folders needs confirm: true")
        return {"removed": service.cleanup(_night(body.night))}

    @app.put("/api/ingest/map")
    def ingest_map(body: MapBody) -> dict[str, Any]:
        lib = state.require_library()
        if body.revisionId and body.revisionId not in lib.revisions:
            raise HTTPException(404, detail="No such target revision")
        if not body.revisionId and not body.catalogId:
            raise HTTPException(422, detail="Give a revisionId or a catalogId")
        cache.map_object(body.object.strip(), body.revisionId, body.catalogId)
        service.poke()
        return {"ok": True}

    @app.delete("/api/ingest/map")
    def ingest_unmap(object: str) -> dict[str, Any]:  # noqa: A002
        removed = cache.unmap_object(object)
        service.poke()
        return {"ok": removed}
