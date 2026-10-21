"""FastAPI app. Read-only over the data root; writes only to var/."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__, api_leaks, api_live, api_organizer, api_planning, api_telescopius, planning
from .cache import HeaderCache
from .config import Config
from .diagnostics.detectors import analyse as analyse_frames, horizon_samples, learn_horizon
from .diagnostics import overrides
from .diagnostics.jobs import DiagnosticsJobs, summarize as summarize_diagnostic
from .library import Library
from .preview import preview as frame_preview
from .scanner import Scanner

log = logging.getLogger(__name__)

THUMB_WIDTH = 720


class Dismissal(BaseModel):
    key: str = Field(min_length=1, max_length=500)
    label: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)


class FrameDecision(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    # "keep" / "reject" overrule astro-eval for this frame; null goes back to its verdict.
    decision: Literal["keep", "reject"] | None = None


class DiagnosticsRequest(BaseModel):
    revisionId: str = Field(min_length=1, max_length=300)
    # One session folder, or omit to queue every session of the revision.
    session: str | None = Field(default=None, max_length=300)


class EngineState:
    def __init__(self, cfg: Config, runner=None):
        self.cfg = cfg
        self.cache = HeaderCache(cfg.db_path)
        self.scanner = Scanner(cfg.data_root, self.cache, cfg.scan_workers)
        self.library: Library | None = None
        self.last_scan = None
        self.last_error: str | None = None
        self._scan_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.loop = None  # the server's event loop (set in lifespan), for sync handlers that touch the observatory
        resolve =lambda rev, sess: self.library.session(rev, sess) if self.library else None  # noqa: E731
        self.jobs = DiagnosticsJobs(cfg, self.cache, resolve, after=self.rescan, **({"runner": runner} if runner else {}))

    def start_scan(self) -> bool:
        with self._lock:
            if self._scan_thread and self._scan_thread.is_alive():
                return False
            self._scan_thread = threading.Thread(target=self._run_scan, name="scan", daemon=True)
            self._scan_thread.start()
            return True

    def rescan(self) -> None:
        """Scan now and wait for it. A scan already running may have walked the disk before the
        latest change, so wait it out and scan again."""
        while True:
            with self._lock:
                running = self._scan_thread if self._scan_thread and self._scan_thread.is_alive() else None
            if running is None and self.start_scan():
                self._scan_thread.join()
                return
            if running is not None:
                running.join()

    def _run_scan(self) -> None:
        try:
            result = self.scanner.scan()
            self.scanner.progress.phase = "building library"
            self.last_scan = result
            self.library = Library(result, self.cfg, self.cache.dismissals())
            self.scanner.progress.phase = "done"
            self.last_error = None
            log.info("scan: %d files, %d read, %.1fs", len(result.frames), result.files_read, result.duration_s)
        except Exception as exc:  # noqa: BLE001
            log.exception("scan failed")
            self.last_error = f"{type(exc).__name__}: {exc}"

    def rebuild(self) -> None:
        """Re-derive the Library from the last scan (after a dismissal change); no disk walk."""
        if self.last_scan is not None:
            self.library = Library(self.last_scan, self.cfg, self.cache.dismissals())

    def require_library(self) -> Library:
        if self.library is None:
            raise HTTPException(503, detail={"message": "The first scan is still running", "progress": self.scanner.progress.as_dict()})
        return self.library


def create_app(cfg: Config, scan_on_start: bool = True, diagnostics_runner=None, observatory: bool | None = None,
               fake_observatory: bool = False) -> FastAPI:
    """`observatory` (default: same as scan_on_start) connects to NINA and PHD2; `fake_observatory` simulates both."""
    state = EngineState(cfg, diagnostics_runner)
    run_observatory = scan_on_start if observatory is None else observatory
    sim_router = None
    if fake_observatory:
        from .observatory import fake

        sim = fake.Sim()
        sim_router = fake.nina_router(sim)
        sim_phd2 = fake.FakePhd2(sim)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        import asyncio

        if scan_on_start:
            state.start_scan()
            # The ingest watcher runs whether or not the Live page is open (it only plans unless mode is Auto).
            state.ingest.start()
        state.loop = asyncio.get_running_loop()
        sim_tasks = []
        if fake_observatory:
            port = await sim_phd2.start()
            sim_tasks.append(asyncio.create_task(sim_router.emit()))
            state.observatory._nina_override = f"http://127.0.0.1:{cfg.port}/fake-nina"
            state.observatory._phd2_override = ("127.0.0.1", port)
            sim.safe = lambda: state.observatory.sky.safe
        alpaca = None
        if run_observatory or fake_observatory:
            await state.observatory.start()
            if cfg.alpaca_port:
                from .observatory.alpaca import AlpacaServer

                alpaca = AlpacaServer(state.observatory.sky, cfg.alpaca_host, cfg.alpaca_port, cfg.alpaca_discovery)
                alpaca.start()
        yield
        if alpaca is not None:
            await asyncio.to_thread(alpaca.stop)
        if run_observatory or fake_observatory:
            await state.observatory.stop()
        for t in sim_tasks:
            t.cancel()
        if fake_observatory:
            await sim_phd2.stop()
        if scan_on_start:
            state.ingest.stop()

    app = FastAPI(title="AstroDashboard engine", version=__version__, lifespan=lifespan)
    app.state.engine = state

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "dataRoot": str(cfg.data_root), "dataRootExists": cfg.data_root.is_dir()}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        lib = state.library
        return {
            "scan": state.scanner.progress.as_dict(),
            "error": state.last_error,
            "ready": lib is not None,
            "scannedAt": lib.overview()["scannedAt"] if lib else None,
            "dataRoot": str(cfg.data_root),
        }

    @app.post("/api/scan")
    def scan() -> dict[str, Any]:
        started = state.start_scan()
        return {"started": started, "scan": state.scanner.progress.as_dict()}

    @app.get("/api/targets")
    def targets() -> dict[str, Any]:
        return state.require_library().overview()

    @app.get("/api/revisions/{rev_id}")
    def revision(rev_id: str) -> dict[str, Any]:
        rev = state.require_library().revision(rev_id)
        if rev is None:
            raise HTTPException(404, detail="No such target revision")
        # Goals edited in the dashboard win over target.json; sessions get the nearest known site.
        stored = state.cache.goals().get(rev_id)
        if stored is not None:
            rev = {**rev, "goals": stored}
        sites = state.cache.sites()
        # Integration left after diagnostics rejection (only where a night was analysed).
        per_filter, analysed = planning.accepted_seconds(rev, state.cache.diagnostic_summaries(), cfg.filter_aliases)
        rev["filters"] = [
            {**f, "acceptedSeconds": per_filter.get(f["filter"], {}).get("goodSeconds", f["seconds"]),
             "analysedSeconds": per_filter.get(f["filter"], {}).get("analysedSeconds", 0.0)}
            for f in rev["filters"]
        ]
        rev["totals"] = {
            **rev["totals"],
            "acceptedSeconds": sum(v["goodSeconds"] for v in per_filter.values()),
            "analysedSeconds": sum(v["analysedSeconds"] for v in per_filter.values()),
        }
        rev["sessions"] = [
            {**s, "siteName": (near := planning.nearest_site(sites, *(s.get("site") or (None, None)))) and near["name"],
             "diagnostic": analysed.get(s["folder"])}
            for s in rev["sessions"]
        ]
        return rev

    @app.get("/api/revisions/{rev_id}/preview.jpg")
    def preview(rev_id: str):
        src = state.require_library().preview_path(rev_id)
        if not src:
            raise HTTPException(404, detail="No finished image")
        thumb = _thumbnail(Path(src), cfg.db_path.parent / "thumbs", rev_id)
        if thumb is None:
            raise HTTPException(404, detail="Preview could not be generated")
        return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/calibration")
    def calibration() -> dict[str, Any]:
        return state.require_library().calibration()

    @app.get("/api/sessions")
    def sessions() -> dict[str, Any]:
        return {"sessions": state.require_library().sessions_index()}

    def _site(rev_id: str, session: str):
        lib = state.library
        sess = lib.session(rev_id, session) if lib else None
        return tuple(sess["site"]) if sess and sess.get("site") else None

    def _analysis(rev_id: str, session: str, result: dict[str, Any]) -> dict[str, Any]:
        lib = state.require_library()
        return analyse_frames(overrides.apply(result), lib.light_headers, _site(rev_id, session))

    @app.get("/api/diagnostics")
    def diagnostics() -> dict[str, Any]:
        rows = state.cache.diagnostic_summaries()
        if state.library is not None:
            for row in rows:
                stored = state.cache.diagnostic(row["revisionId"], row["session"])
                if stored:
                    found = _analysis(row["revisionId"], row["session"], stored["result"])["findings"]
                    row["findings"] = [
                        {"code": f["code"], "level": f["level"], "title": f["title"], "frames": len(f["frames"])}
                        for f in found if f["level"] in ("warn", "bad")
                    ]
        return {"jobs": state.jobs.list(), "results": rows}

    @app.get("/api/horizon")
    def horizon() -> dict[str, Any]:
        lib = state.require_library()
        samples: list = []
        nights = 0
        for row in state.cache.diagnostic_summaries():
            stored = state.cache.diagnostic(row["revisionId"], row["session"])
            if stored:
                s = horizon_samples(overrides.apply(stored["result"]), lib.light_headers, _site(row["revisionId"], row["session"]))
                samples += s
                nights += bool(s)
        return {
            "nights": nights,
            "samples": len(samples),
            "obstructed": sum(1 for *_, o in samples if o),
            "bins": learn_horizon(samples),
            "points": [{"az": round(az, 1), "alt": round(alt, 1), "obstructed": o} for az, alt, o in samples],
        }

    @app.post("/api/diagnostics/run")
    def run_diagnostics(body: DiagnosticsRequest) -> dict[str, Any]:
        lib = state.require_library()
        rev = lib.revision(body.revisionId)
        if rev is None:
            raise HTTPException(404, detail="No such target revision")
        folders = [body.session] if body.session else [s["folder"] for s in rev["sessions"] if s["totals"]["lights"]]
        if body.session and lib.session(body.revisionId, body.session) is None:
            raise HTTPException(404, detail="No such session")
        return {"jobs": [state.jobs.submit(body.revisionId, f) for f in folders]}

    def _user_rules(rev: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """Apply the user's decisions to astro-eval's notes (PLAN.md): HDR flats are not
        reported on finished targets. Returns a copy; the stored result is untouched."""
        if rev["stage"]["value"] != "finished":
            return result
        import copy

        out = copy.deepcopy(result)
        readout = lambda n: "Readout mode is" in n["text"]  # noqa: E731
        rank = {"ok": 0, "info": 0, "warn": 1, "bad": 2}
        for f in out.get("flats", []):
            s = f.get("summary")
            if s and any(readout(n) for n in s["notes"]):
                s["notes"] = [n for n in s["notes"] if not readout(n)]
                if s["verdict"] == "warn" and not any(n["level"] in ("warn", "bad") for n in s["notes"]):
                    s["verdict"] = "good"
        cal = out.get("calibration")
        if cal:
            for row in cal["rows"]:
                if any(readout(i) for i in row["issues"]):
                    row["issues"] = [i for i in row["issues"] if not readout(i)]
                    worst = max((rank.get(i["level"], 0) for i in row["issues"]), default=0)
                    if row["status"] == "warn" and worst < 1:
                        row["status"] = "ok"
            cal["common"] = [i for i in cal["common"] if not readout(i)]
        return out

    @app.get("/api/diagnostics/{rev_id}/{session}")
    def diagnostic(rev_id: str, session: str) -> dict[str, Any]:
        lib = state.require_library()
        rev = lib.revision(rev_id)
        sess = lib.session(rev_id, session)
        if rev is None or sess is None:
            raise HTTPException(404, detail="No such session")
        stored = state.cache.diagnostic(rev_id, session)
        jobs = [j for j in state.jobs.list() if j["revisionId"] == rev_id and j["session"] == session]
        return {
            "revision": {"id": rev["id"], "folder": rev["folder"], "catalogId": rev["catalogId"], "name": rev["name"]},
            "session": {k: sess[k] for k in ("folder", "date", "path", "totals", "conditions", "start", "end")},
            "sessions": [s["folder"] for s in rev["sessions"] if s["totals"]["lights"]],
            "job": jobs[-1] if jobs else None,
            "stored": {**stored, "result": _user_rules(rev, overrides.apply(stored["result"]))} if stored else None,
            "analysis": _analysis(rev_id, session, stored["result"]) if stored else None,
        }

    @app.post("/api/diagnostics/{rev_id}/{session}/frames")
    def decide_frame(rev_id: str, session: str, body: FrameDecision) -> dict[str, Any]:
        """Keep or reject one light by hand, over astro-eval's verdict (survives re-runs)."""
        stored = state.cache.diagnostic(rev_id, session)
        if stored is None:
            raise HTTPException(404, detail="This night has not been analysed")
        result = stored["result"]
        if body.path not in overrides.light_paths(result):
            raise HTTPException(404, detail="No such frame in this analysis")
        decided = dict(result.get("overrides") or {})
        if body.decision is None:
            decided.pop(body.path, None)
        else:
            decided[body.path] = body.decision
        result["overrides"] = decided
        state.cache.update_diagnostic(rev_id, session, summarize_diagnostic(overrides.apply(result)), result)
        return diagnostic(rev_id, session)

    @app.get("/api/frames/preview.jpg")
    def preview(path: str):
        root = cfg.data_root.resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404, detail="No such frame")
        try:
            out = frame_preview(target, cfg.db_path.parent / "previews")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, detail=f"Preview failed: {exc}") from exc
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/dismissals")
    def dismissals() -> dict[str, Any]:
        return {"dismissals": list(state.cache.dismissals().values())}

    @app.post("/api/dismissals")
    def dismiss(body: Dismissal) -> dict[str, Any]:
        state.require_library()
        note = (body.note or "").strip() or None
        state.cache.dismiss(body.key, body.key.split("|", 1)[0], body.label, note)
        state.rebuild()
        return {"ok": True}

    @app.delete("/api/dismissals")
    def undismiss(key: str) -> dict[str, Any]:
        state.require_library()
        removed = state.cache.undismiss(key)
        state.rebuild()
        return {"ok": removed}

    api_planning.register(app, state)
    api_live.register(app, state)
    if sim_router is not None:
        app.include_router(sim_router)
    api_organizer.register(app, state)
    api_telescopius.register(app, state)
    api_leaks.register(app, state)
    return app


def _thumbnail(src: Path, out_dir: Path, rev_id: str) -> Path | None:
    """Downscaled JPEG of a .Finished render, cached in var/thumbs (source opened read-only)."""
    try:
        mtime = int(src.stat().st_mtime)
    except OSError:
        return None
    out = out_dir / f"{rev_id}-{mtime}.jpg"
    if out.is_file():
        return out
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        out_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.draft("RGB", (THUMB_WIDTH * 2, THUMB_WIDTH * 2))
            im = im.convert("RGB")
            im.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=85)
        tmp.replace(out)  # inside var/thumbs only
        return out
    except Exception:  # noqa: BLE001
        log.exception("thumbnail failed for %s", src)
        return None
