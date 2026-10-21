"""Live (direct NINA + PHD2, PLAN §5.3) and auto-ingest endpoints."""

from __future__ import annotations

import asyncio
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .frames import canonical_filter, normalize_kind
from .headers import read_header
from .ingest import paths as ingest_paths
from .ingest.planner import NIGHT_DIR_RE, evening_of, match_target, split_panel
from .ingest.service import IngestService
from .library import slugify
from .observatory import advisor as adv
from .observatory import frames as obs_frames
from .observatory.nina import NinaError, NinaUnavailable
from .observatory.runtime import PHD2_ERRORS, Observatory
from .preview import preview as frame_preview


class ParamBody(BaseModel):
    axis: Literal["ra", "dec"]
    param: str = Field(min_length=1, max_length=60)
    value: float = Field(ge=0, le=100)


class ExposureBody(BaseModel):
    ms: int = Field(ge=50, le=60_000)


class DecModeBody(BaseModel):
    mode: Literal["Off", "Auto", "North", "South"]


class ControlBody(BaseModel):
    action: Literal["guide", "stop", "pause", "resume", "dither"]
    recalibrate: bool = False


class ModeBody(BaseModel):
    mode: Literal["off", "dry-run", "auto"]


class NightBody(BaseModel):
    night: str = Field(min_length=10, max_length=20)


class CleanupBody(NightBody):
    # Removing folders is a deletion (even empty ones): the UI asks, and the request must say so.
    confirm: bool = False


class TonightBody(BaseModel):
    revisionId: str | None = Field(default=None, max_length=300)  # noqa: N815 — None clears the choice
    object: str | None = Field(default=None, max_length=200)  # one NINA object; omit for every light of the night
    night: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # default: the night in progress
    # Sending frames to a revision that is not "acquiring" (finished, processed) must be confirmed.
    confirm: bool = False


class FollowBody(BaseModel):
    follow: bool | None = None  # follow NINA's target
    finished: bool | None = None  # …also onto finished targets (they are reopened as acquiring)


class StatusBody(BaseModel):
    revisionId: str = Field(min_length=1, max_length=300)  # noqa: N815
    status: Literal["acquiring", "finished"]


class MapBody(BaseModel):
    object: str = Field(min_length=1, max_length=200)
    revisionId: str | None = Field(default=None, max_length=300)  # noqa: N815
    catalogId: str | None = Field(default=None, max_length=200)  # noqa: N815


def register(app: FastAPI, state) -> None:
    cache = state.cache
    obs = Observatory(lambda: cache.get_json("settings") or {}, cache,
                      lambda: ingest_paths.download_root(state.cfg.data_root, cache.get_json("settings")))
    state.observatory = obs

    def on_moved() -> None:
        state.start_scan()  # pick up the new nights in the Library

    service = IngestService(state.cfg, cache, lambda: state.library, on_moved, obs.sequence_running)
    state.ingest = service

    def _night(n: str) -> str:
        if not NIGHT_DIR_RE.match(n) or os.sep in n or "/" in n:
            raise HTTPException(422, detail="Not a night folder name of the download folder")
        return n

    # ------------------------------------------------------------------ live

    @app.get("/api/live/state")
    def live_state(graph: float = 180) -> dict[str, Any]:
        snap = obs.snapshot(graph_s=min(max(graph, 30), 3600))
        try:
            snap["framing"] = _latest_framing()
        except Exception as exc:  # noqa: BLE001 — never at the cost of the live page
            snap["framing"] = {"status": "unmeasured", "error": f"{type(exc).__name__}: {exc}"}
        return snap

    last_header: dict[str, Any] = {"key": None, "header": None}

    def _latest_framing() -> dict[str, Any] | None:
        """The newest light on disk against its target's previous night (None: no light to check)."""
        latest = obs.latest_frame()
        path = (latest.get("path") if latest["source"] == "file" else latest.get("fallback")) if latest else None
        lib = state.library
        if not path or lib is None:
            return None
        key = (path, os.path.getsize(path))
        if last_header["key"] != key:
            last_header["key"], last_header["header"] = key, read_header(Path(path))
        header = last_header["header"]
        if normalize_kind(header.get("IMAGETYP"), None, Path(path).name) != "LIGHT":
            return None
        night = evening_of(header.get("DATE-LOC")) or evening_of(header.get("DATE-OBS"))
        obj, panel = split_panel(header.get("OBJECT"))
        return state.framing.live(path, _revision_of(path, obj, night, lib), panel, night.isoformat() if night else None)

    def _revision_of(path: str, obj: str | None, night, lib) -> str | None:
        """Where auto-ingest sends this light: its target folder once ingested, else tonight's choice,
        the ingest plan's routing, or the NINA object matched to a target."""
        p = Path(path).resolve()
        try:
            rel = None if p.is_relative_to(service.download_root().resolve()) else p.relative_to(state.cfg.data_root.resolve())
        except ValueError:
            rel = None
        if rel is not None and rel.parts:
            rid = slugify(rel.parts[0])
            return rid if rid in lib.revisions else None
        if night is None or not obj:
            return None
        chosen = service.assignments().get(night.isoformat()) or {}
        rid = (chosen.get("objects") or {}).get(obj.casefold()) or chosen.get("all")
        if rid in lib.revisions:
            return rid
        planned = ((service.last_plan.objects if service.last_plan else {}).get(night.isoformat()) or {}).get(obj) or {}
        if planned.get("revisionId") in lib.revisions:
            return planned["revisionId"]
        target, _ = match_target(obj, service.targets(lib), service.object_map(lib), True)
        return target.revision_id if target else None

    @app.get("/api/live/frames")
    def live_frames(filter: str | None = None, all: bool = False, limit: int = 100) -> dict[str, Any]:  # noqa: A002
        """Tonight's frames: counts per filter over the whole night, and the newest `limit` of one filter (or all)."""
        rows = obs.frames if all else [f for f in obs.frames if f.get("type") == "LIGHT"]

        def key(f: dict) -> str:
            return canonical_filter(f.get("filter"), state.cfg.filter_aliases) or "—"

        selected = [f for f in rows if not filter or key(f) == filter]
        return {
            "total": len(rows),
            "counts": dict(Counter(key(f) for f in rows)),
            "selected": len(selected),
            "frames": [{**f, "filterKey": key(f)} for f in selected[: max(1, min(limit, 5000))]],
        }

    @app.get("/api/live/frame.jpg")
    async def live_frame(v: str | None = None):  # noqa: ARG001 — cache buster: the URL changes with the frame
        latest = obs.latest_frame()
        if latest is None:
            raise HTTPException(404, detail="No frame yet")
        cache_hdr = {"Cache-Control": "max-age=86400"}
        if latest["source"] == "file":
            try:
                jpg = await asyncio.to_thread(frame_preview, Path(latest["path"]), state.cfg.db_path.parent / "previews")
            except Exception as exc:  # noqa: BLE001 — a frame still landing or an odd file
                raise HTTPException(404, detail=f"Could not render {latest['name']}: {exc}") from exc
            return FileResponse(jpg, media_type="image/jpeg", headers=cache_hdr)
        try:
            if obs.nina is None:
                raise NinaUnavailable("NINA is not connected")
            data, ctype = await obs.nina.image_jpeg(int(latest["index"]))
        except (NinaError, NinaUnavailable) as exc:
            # NINA could not render it: show the last complete file from the download folder rather than nothing.
            if latest.get("fallback"):
                jpg = await asyncio.to_thread(frame_preview, Path(latest["fallback"]), state.cfg.db_path.parent / "previews")
                return FileResponse(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
            raise HTTPException(502, detail=str(exc)) from exc
        return Response(data, media_type=ctype, headers=cache_hdr)

    @app.get("/api/live/guide-star.png")
    async def live_guide_star(size: int = 31, v: str | None = None):  # noqa: ARG001
        if not obs.phd2 or not obs.phd2.connected:
            raise HTTPException(404, detail="PHD2 is not connected")
        try:
            png = await asyncio.to_thread(obs_frames.star_png, await obs.phd2.star_image(min(max(size, 15), 63)))
        except (*PHD2_ERRORS, ValueError) as exc:  # "no star selected" is a state, not a fault
            raise HTTPException(404, detail=str(exc)) from exc
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    async def _phd2(coro) -> dict[str, Any]:
        try:
            result = await coro
        except PHD2_ERRORS as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return {"ok": True, **(result if isinstance(result, dict) else {})}

    @app.post("/api/live/guiding/param")
    async def guiding_param(body: ParamBody) -> dict[str, Any]:
        return await _phd2(obs.set_param(body.axis, body.param, body.value))

    @app.post("/api/live/guiding/exposure")
    async def guiding_exposure(body: ExposureBody) -> dict[str, Any]:
        return await _phd2(obs.set_exposure(body.ms))

    @app.post("/api/live/guiding/dec-mode")
    async def guiding_dec_mode(body: DecModeBody) -> dict[str, Any]:
        return await _phd2(obs.set_dec_mode(body.mode))

    @app.post("/api/live/guiding/control")
    async def guiding_control(body: ControlBody) -> dict[str, Any]:
        if not obs.phd2 or not obs.phd2.connected:
            raise HTTPException(502, detail="PHD2 is not connected")
        p = obs.phd2
        action = {
            "guide": lambda: p.guide(body.recalibrate),
            "stop": p.stop,
            "pause": lambda: p.set_paused(True),
            "resume": lambda: p.set_paused(False),
            "dither": p.dither,
        }[body.action]
        return await _phd2(action())

    @app.post("/api/live/advisor/analyze")
    async def advisor_analyze() -> dict[str, Any]:
        if not obs.adv_settings()["enabled"]:
            raise HTTPException(409, detail="The AI advisor is off")
        try:
            obs.start_analysis()
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"started": True}

    @app.post("/api/live/sky/clear")
    def sky_clear() -> dict[str, Any]:
        """Tell NINA the sky is clear until the next guiding run (you know better: it is a building, or it is fine)."""
        if obs.sky.safe:
            raise HTTPException(409, detail="The sky monitor already says clear")
        obs.sky.clear_now()
        return {"ok": True}

    @app.post("/api/live/advisor/suggestions/{sid}/apply")
    async def advisor_apply(sid: str) -> dict[str, Any]:
        s = obs.advisor.suggestion(sid)
        if s is None:
            raise HTTPException(404, detail="No such suggestion")
        if s["status"] != "open":
            raise HTTPException(409, detail=f"This suggestion is {s['status']}")
        if s["param"] == adv.ALGORITHM:
            raise HTTPException(409, detail=f"PHD2 has no command to switch the algorithm: pick {s['value']} for {s['axis'].upper()} "
                                            "in PHD2's Brain (Algorithms tab), then click Done")
        current = ((obs.phd2.state.params if obs.phd2 else {}).get(s["axis"]) or {}).get(s["param"])
        if current is None or abs(current - s["current"]) > 1e-6:
            s["status"] = "stale"
            obs.advisor.save()
            raise HTTPException(409, detail="The parameter changed since this suggestion was made; ask again")
        return await _phd2(obs.set_param(s["axis"], s["param"], s["value"], source="ai", suggestion=sid))

    @app.post("/api/live/advisor/suggestions/{sid}/done")
    async def advisor_done(sid: str) -> dict[str, Any]:
        """An algorithm switch, made by you in PHD2's Brain: measured from now on."""
        try:
            return await _phd2(obs.algorithm_done(sid))
        except LookupError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.post("/api/live/advisor/suggestions/{sid}/dismiss")
    def advisor_dismiss(sid: str) -> dict[str, Any]:
        return {"ok": obs.advisor.dismiss(sid)}

    @app.post("/api/live/advisor/changes/{cid}/revert")
    async def advisor_revert(cid: str) -> dict[str, Any]:
        c = next((c for c in obs.advisor.changes if c["id"] == cid), None)
        if c is None or c["axis"] not in ("ra", "dec"):
            raise HTTPException(404, detail="No such parameter change")
        if c["param"] == adv.ALGORITHM:
            raise HTTPException(409, detail=f"PHD2 has no command to switch the algorithm: pick {c['old']} again in PHD2's Brain")
        return await _phd2(obs.set_param(c["axis"], c["param"], c["old"], source="revert"))

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
        # Lights already moved into tonight's NIGHT_ folders, per revision (the journal keeps the destination).
        tonight_moved: dict[str, int] = defaultdict(int)
        for r in journal:
            if r["kind"] == "LIGHT" and not r["undone"] and not r["error"] and f"NIGHT_{st['tonight']}" in r["dst"]:
                tonight_moved[r["revision"]] += 1
        return {
            **st,
            "nina": _nina_target(lib, st),
            "tonightMoved": dict(tonight_moved),
            "journal": {k: dict(v) for k, v in sorted(nights.items(), reverse=True)},
            "recent": journal[:200],
            "objectMap": sorted(service.object_map(lib).values(), key=lambda m: m["object"].casefold()) if lib else [],
            "targets": [
                {"id": r["id"], "folder": r["folder"], "catalogId": r["catalogId"], "name": r["name"], "stage": r["stage"]["value"], "revision": r["revision"]}
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

    def _nina_target(lib, st: dict[str, Any]) -> dict[str, Any] | None:
        """NINA's current target and where following it sends frames (or why it does not)."""
        name = obs.sequence.get("target") or next((f["target"] for f in obs.frames if f.get("target")), None)
        if not name or lib is None:
            return None
        target, problem = match_target(name, service.targets(lib), service.object_map(lib), st["followFinished"])
        if target is None and not st["followFinished"]:
            # Say which finished revision it would have been, so the warning can name it.
            target_any, _ = match_target(name, service.targets(lib), service.object_map(lib), True)
            return {"object": name, "revisionId": None, "blockedBy": target_any.revision_id if target_any else None,
                    "target": target_any.target if target_any else None, "stage": target_any.stage if target_any else None,
                    "problem": problem, "running": obs.sequence.get("status") == "running"}
        return {"object": name, "revisionId": target.revision_id if target else None, "blockedBy": None,
                "target": target.target if target else None, "stage": target.stage if target else None,
                "problem": problem, "running": obs.sequence.get("status") == "running"}

    @app.post("/api/ingest/follow")
    def ingest_follow(body: FollowBody) -> dict[str, Any]:
        return service.set_follow(body.follow, body.finished)

    @app.post("/api/ingest/status")
    def ingest_status_set(body: StatusBody) -> dict[str, Any]:
        """Mark a target acquiring or finished in its target.json (e.g. "mark finished again" after a reopen)."""
        state.require_library()
        try:
            previous = service.set_status(body.revisionId, body.status)
        except KeyError as exc:
            raise HTTPException(404, detail="No such target revision") from exc
        return {"ok": True, "previous": previous}

    @app.post("/api/ingest/tonight")
    def ingest_tonight(body: TonightBody) -> dict[str, Any]:
        """Tonight's target: the night's lights (or one object's) go to this revision, whatever the OBJECT mapping says."""
        if body.revisionId:
            lib = state.require_library()
            rev = lib.revisions.get(body.revisionId)
            if rev is None:
                raise HTTPException(404, detail="No such target revision")
            stage = rev["stage"]["value"]
            if stage != "acquiring" and not body.confirm:
                raise HTTPException(409, detail=f"{rev['folder']} is marked \"{stage}\"; send confirm: true to use it anyway")
        a = service.assign(body.revisionId, body.object, body.night)
        return {"ok": True, "assignment": a}

    @app.post("/api/ingest/remove-copies")
    def ingest_remove_copies(body: CleanupBody) -> dict[str, Any]:
        """Remove copies in the download folder of frames already ingested (re-synced by robocopy); each is byte-compared first."""
        if not body.confirm:
            raise HTTPException(422, detail="Removing files needs confirm: true")
        state.require_library()
        return service.remove_ingested_copies(_night(body.night))

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
