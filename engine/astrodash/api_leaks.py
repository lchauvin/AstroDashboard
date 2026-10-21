"""Light-leak finder endpoints: tests made of spot checks (phase 1) and dark sets (phase 2).

Phase 1, spot checks: single frames, each with a flashlight on one spot of the rig,
judged one by one against frames taken without it — where does light get in?

Phase 2, dark sets: the rig with a fix in place. Each set is compared with the one
before it (the iteration: the last set becomes the new baseline), and any two sets
can be compared on demand.

Both are analysed in the background. Tests live in the dashboard database; arrays
in var/leaks/. The frames themselves are only read.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import leaks

log = logging.getLogger(__name__)


class NewTest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TestPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)


class Group(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    exposure: float | None = None
    setTemp: float | None = None  # noqa: N815
    readout: str | None = Field(default=None, max_length=100)
    gain: float | None = None
    offset: float | None = None
    binning: int | None = None
    filter: str | None = Field(default=None, max_length=100)


class NewSet(Group):
    comment: str = Field(default="", max_length=2000)


class CommentPatch(BaseModel):
    comment: str = Field(max_length=2000)


class NewCheck(BaseModel):
    probe: Group
    # Frames without the flashlight, when they are in another folder or setting.
    reference: Group | None = None
    comment: str = Field(default="", max_length=2000)


class FramePatch(BaseModel):
    index: int = Field(ge=0)
    role: Literal["reference", "probe"] | None = None
    comment: str | None = Field(default=None, max_length=500)


class CheckPatch(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)
    frames: list[FramePatch] = Field(default_factory=list, max_length=500)


WANT_KEYS = (*leaks.SETTING_KEYS, "filter")


def register(app: FastAPI, state) -> None:
    cfg = state.cfg
    cache = state.cache
    out_dir = cfg.db_path.parent / "leaks"
    lock = threading.RLock()
    jobs: queue.Queue[tuple[str, str, str]] = queue.Queue()
    pending: set[str] = set()
    progress: dict[str, dict[str, int]] = {}
    compared: dict[tuple, dict[str, Any]] = {}
    evaluated: dict[tuple, tuple[dict[str, Any], dict[str, Any]]] = {}
    worker: dict[str, threading.Thread | None] = {"t": None}

    def _resolve(folder: str) -> Path:
        p = Path(folder.strip().strip('"'))
        return p if p.is_absolute() else cfg.data_root / p

    def _arrays(set_id: str) -> dict[str, np.ndarray]:
        path = out_dir / f"{set_id}.npz"
        if not path.is_file():
            raise HTTPException(409, detail="Not analysed yet")
        with np.load(path) as z:
            return {k: z[k] for k in z.files}

    def _forget(set_id: str) -> None:
        """A set's or check's arrays, in var/leaks only (ids are generated here, never paths)."""
        (out_dir / f"{set_id}.npz").unlink(missing_ok=True)

    def _update(test_id: str, coll: str, item_id: str, **changes) -> None:
        with lock:
            t = cache.leak_test(test_id)
            if t is None:
                return
            for s in t.get(coll, []):
                if s["id"] == item_id:
                    s.update(changes)
            cache.save_leak_test(test_id, t)

    def _analyse(coll: str, item: dict[str, Any], report) -> tuple[dict[str, Any], dict[str, np.ndarray], dict[str, Any]]:
        """(result, arrays, other changes) for a set or a check."""
        if coll == "sets":
            files = leaks.set_files(_resolve(item["folder"]), item["want"], cfg.gain_ignored)
            if not files:
                raise ValueError("no dark frames at this setting in the folder")
            summary, arrays = leaks.analyse_set(files, report)
            return summary, arrays, {}
        files = []
        for f in item["frames"]:
            p = Path(f["path"])
            if not p.is_file():
                raise ValueError(f"{p.name} is no longer in {p.parent}")
            files.append((p, {"time": f.get("time"), "ccdTemp": f.get("ccdTemp")}))
        rows, stack = leaks.analyse_check(files, report)
        changes = {}
        if not any(f["role"] == "reference" for f in item["frames"]):
            # Nothing marked: start from the darkest frames; you can change it frame by frame.
            ref = set(leaks.auto_reference(rows))
            changes["frames"] = [{**f, "role": "reference" if i in ref else "probe"} for i, f in enumerate(item["frames"])]
            changes["autoReference"] = True
        return {"rows": rows}, {"stack": stack}, changes

    def _run() -> None:
        while True:
            coll, test_id, item_id = jobs.get()
            try:
                t = cache.leak_test(test_id)
                item = next((x for x in t.get(coll, []) if x["id"] == item_id), None) if t else None
                if item is None:
                    continue
                _update(test_id, coll, item_id, status="running", error=None)
                result, arrays, changes = _analyse(coll, item, lambda i, n: progress.__setitem__(item_id, {"done": i, "total": n}))
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp = out_dir / f"{item_id}.tmp.npz"
                np.savez(tmp, **arrays)
                tmp.replace(out_dir / f"{item_id}.npz")  # inside var/leaks only
                _update(test_id, coll, item_id, status="done", result=result, analysedAt=time.time(), error=None, **changes)
                t = cache.leak_test(test_id)
                if t is None or not any(x["id"] == item_id for x in t.get(coll, [])):
                    _forget(item_id)  # removed while it ran
            except Exception as exc:  # noqa: BLE001
                log.exception("leak %s %s failed", coll, item_id)
                _update(test_id, coll, item_id, status="error", error=f"{exc}")
            finally:
                pending.discard(item_id)
                progress.pop(item_id, None)
                jobs.task_done()

    def _submit(coll: str, test_id: str, item_id: str) -> None:
        with lock:
            if item_id in pending:
                return
            pending.add(item_id)
            if worker["t"] is None or not worker["t"].is_alive():
                worker["t"] = threading.Thread(target=_run, name="leaks", daemon=True)
                worker["t"].start()
        jobs.put((coll, test_id, item_id))

    def _compare(base: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
        key = (base["id"], base.get("analysedAt"), comp["id"], comp.get("analysedAt"))
        if key not in compared:
            if len(compared) > 64:
                compared.clear()
            compared[key] = leaks.compare(base["result"], comp["result"], _arrays(base["id"]), _arrays(comp["id"]))
        return compared[key]

    def _evaluate(check: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        roles = [f["role"] for f in check["frames"]]
        key = (check["id"], check.get("analysedAt"), tuple(roles))
        if key not in evaluated:
            if len(evaluated) > 4:  # each holds its check's frame stack (~2 MB a frame)
                evaluated.clear()
            stack = _arrays(check["id"])["stack"]
            evaluation, extra = leaks.evaluate_check(check["result"]["rows"], stack, roles)
            evaluated[key] = (evaluation, {**extra, "stack": stack})
        return evaluated[key]

    def _test(test_id: str) -> dict[str, Any]:
        t = cache.leak_test(test_id)
        if t is None:
            raise HTTPException(404, detail="No such leak test")
        t.setdefault("checks", [])
        return t

    def _item(t: dict[str, Any], coll: str, item_id: str) -> dict[str, Any]:
        s = next((x for x in t.get(coll, []) if x["id"] == item_id), None)
        if s is None:
            raise HTTPException(404, detail="No such dark set" if coll == "sets" else "No such spot check")
        return s

    def _resume(t: dict[str, Any], coll: str, s: dict[str, Any]) -> None:
        if s["status"] in ("queued", "running") and s["id"] not in pending:
            _submit(coll, t["id"], s["id"])  # e.g. the engine restarted mid-analysis

    def _view(t: dict[str, Any]) -> dict[str, Any]:
        """The test with live progress, each set's verdict against the set before it, and each check's evaluation."""
        prev = None
        sets = []
        for s in t["sets"]:
            _resume(t, "sets", s)
            row = {**s, "progress": progress.get(s["id"])}
            if prev is not None and prev["status"] == "done" and s["status"] == "done":
                try:
                    c = _compare(prev, s)
                    row["vsPrevious"] = {"baseId": prev["id"], "verdict": c["verdict"], "headline": c["headline"],
                                         "levelDelta": c["level"]["delta"], "findings": len(c["findings"])}
                except (ValueError, HTTPException) as exc:
                    row["vsPrevious"] = {"baseId": prev["id"], "verdict": "error", "headline": getattr(exc, "detail", str(exc))}
            sets.append(row)
            prev = s
        checks = []
        for c in t.get("checks", []):
            _resume(t, "checks", c)
            row = {**c, "progress": progress.get(c["id"]), "frames": [{k: v for k, v in f.items() if k != "path"} for f in c["frames"]]}
            row.pop("result", None)
            if c["status"] == "done":
                try:
                    row["evaluation"] = _evaluate(c)[0]
                except (ValueError, HTTPException) as exc:
                    row["status"], row["error"] = "error", getattr(exc, "detail", str(exc))
            checks.append(row)
        return {**t, "sets": sets, "checks": checks}

    @app.get("/api/leaks")
    def tests() -> dict[str, Any]:
        rows = []
        for t in cache.leak_tests():
            items = t["sets"] + t.get("checks", [])
            latest = max(items, key=lambda x: x.get("added", 0), default=None)
            rows.append({
                "id": t["id"], "name": t["name"], "created": t["created"], "sets": len(t["sets"]), "checks": len(t.get("checks", [])),
                "lastComment": latest["comment"] if latest else None,
                "updated": latest.get("added", t["created"]) if latest else t["created"],
            })
        return {"tests": rows}

    @app.get("/api/leaks/library-groups")
    def library_groups() -> dict[str, Any]:
        """Raw dark groups of the calibration library, as ready-made set sources."""
        lib = state.library
        if lib is None:
            return {"ready": False, "groups": []}
        root = cfg.data_root / ".Calibration"
        groups = [
            {"folder": str(root / g.build / g.folder), "build": g.build, "kind": g.kind, "exposure": g.exposure,
             "setTemp": g.temp, "readout": g.readout, "gain": g.gain, "offset": g.offset, "binning": g.binning,
             "instrume": g.instrume, "frames": g.frames, "filter": None}
            for g in lib.darks.raw if g.kind == "dark"
        ]
        return {"ready": True, "groups": groups}

    @app.get("/api/leaks/browse")
    def browse(folder: str = Query(min_length=1, max_length=1000)) -> dict[str, Any]:
        path = _resolve(folder)
        if not path.is_dir():
            raise HTTPException(404, detail=f"No such folder: {path}")
        return leaks.find_groups(path, cfg.gain_ignored)

    @app.post("/api/leaks")
    def create(body: NewTest) -> dict[str, Any]:
        test_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        cache.save_leak_test(test_id, {"name": body.name.strip(), "notes": "", "sets": [], "checks": [], "created": time.time()})
        return _view(_test(test_id))

    @app.get("/api/leaks/{test_id}")
    def test(test_id: str) -> dict[str, Any]:
        return _view(_test(test_id))

    @app.patch("/api/leaks/{test_id}")
    def patch(test_id: str, body: TestPatch) -> dict[str, Any]:
        with lock:
            t = _test(test_id)
            if body.name is not None:
                t["name"] = body.name.strip()
            if body.notes is not None:
                t["notes"] = body.notes
            cache.save_leak_test(test_id, t)
        return _view(t)

    @app.delete("/api/leaks/{test_id}")
    def delete(test_id: str) -> dict[str, Any]:
        """Forget a test: its database row and its arrays in var/leaks (never the frames)."""
        with lock:
            t = _test(test_id)
            cache.delete_leak_test(test_id)
        for s in t["sets"] + t["checks"]:
            _forget(s["id"])
        return {"ok": True}

    # ------------------------------------------------------------------ phase 2: dark sets

    @app.post("/api/leaks/{test_id}/sets")
    def add_set(test_id: str, body: NewSet) -> dict[str, Any]:
        path = _resolve(body.folder)
        if not path.is_dir():
            raise HTTPException(404, detail=f"No such folder: {path}")
        want = {k: getattr(body, k) for k in WANT_KEYS}
        set_id = uuid.uuid4().hex[:12]
        with lock:
            t = _test(test_id)
            t["sets"].append({"id": set_id, "folder": str(path), "want": want, "comment": body.comment.strip(),
                              "added": time.time(), "status": "queued", "result": None, "error": None})
            cache.save_leak_test(test_id, t)
        _submit("sets", test_id, set_id)
        return _view(_test(test_id))

    @app.patch("/api/leaks/{test_id}/sets/{set_id}")
    def patch_set(test_id: str, set_id: str, body: CommentPatch) -> dict[str, Any]:
        with lock:
            t = _test(test_id)
            _item(t, "sets", set_id)["comment"] = body.comment.strip()
            cache.save_leak_test(test_id, t)
        return _view(t)

    def _remove(test_id: str, coll: str, item_id: str) -> dict[str, Any]:
        with lock:
            t = _test(test_id)
            _item(t, coll, item_id)
            t[coll] = [s for s in t[coll] if s["id"] != item_id]
            cache.save_leak_test(test_id, t)
        if item_id not in pending:
            _forget(item_id)
        return _view(t)

    def _rerun(test_id: str, coll: str, item_id: str) -> dict[str, Any]:
        _item(_test(test_id), coll, item_id)
        _update(test_id, coll, item_id, status="queued", error=None)
        _submit(coll, test_id, item_id)
        return _view(_test(test_id))

    @app.delete("/api/leaks/{test_id}/sets/{set_id}")
    def delete_set(test_id: str, set_id: str) -> dict[str, Any]:
        """Drop a set from the test (its arrays in var/leaks go too; the frames stay where they are)."""
        return _remove(test_id, "sets", set_id)

    @app.post("/api/leaks/{test_id}/sets/{set_id}/rerun")
    def rerun(test_id: str, set_id: str) -> dict[str, Any]:
        return _rerun(test_id, "sets", set_id)

    def _pair(test_id: str, base: str, comp: str) -> tuple[dict[str, Any], dict[str, Any]]:
        t = _test(test_id)
        b, c = _item(t, "sets", base), _item(t, "sets", comp)
        if b["status"] != "done" or c["status"] != "done":
            raise HTTPException(409, detail="Both sets must be analysed first")
        return b, c

    @app.get("/api/leaks/{test_id}/compare")
    def compare(test_id: str, base: str, comp: str) -> dict[str, Any]:
        b, c = _pair(test_id, base, comp)
        try:
            return {"base": base, "comp": comp, **_compare(b, c)}
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    png = {"media_type": "image/png", "headers": {"Cache-Control": "max-age=3600"}}

    @app.get("/api/leaks/{test_id}/sets/{set_id}/master.png")
    def master_png(test_id: str, set_id: str, lo: float | None = None, hi: float | None = None, center: bool = False):
        _item(_test(test_id), "sets", set_id)
        return Response(leaks.render_master(_arrays(set_id)["master"], lo, hi, center), **png)

    @app.get("/api/leaks/{test_id}/diff.png")
    def diff_png(test_id: str, base: str, comp: str, mode: str = "structure", theme: str = "light"):
        b, c = _pair(test_id, base, comp)
        try:
            d = _compare(b, c)["display"]
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        diff = _arrays(comp)["master"] - _arrays(base)["master"]
        # structure: background out, ±5σ of the noise (hard); fit: background out, scaled to the data; absolute: with the level.
        limit = {"fit": d["fitLimit"], "absolute": d["absoluteLimit"]}.get(mode, d["structureLimit"])
        return Response(leaks.render_diff(diff, limit, center=mode != "absolute", theme="dark" if theme == "dark" else "light"), **png)

    # ------------------------------------------------------------------ phase 1: spot checks

    def _frames(group: Group, role: str) -> list[dict[str, Any]]:
        path = _resolve(group.folder)
        if not path.is_dir():
            raise HTTPException(404, detail=f"No such folder: {path}")
        files = leaks.set_files(path, {k: getattr(group, k) for k in WANT_KEYS}, cfg.gain_ignored)
        return [{"path": str(p), "file": p.name, "time": s["time"], "ccdTemp": s["ccdTemp"], "role": role, "comment": ""}
                for p, s in files]

    @app.post("/api/leaks/{test_id}/checks")
    def add_check(test_id: str, body: NewCheck) -> dict[str, Any]:
        frames = _frames(body.probe, "probe")
        if body.reference is not None:
            frames = _frames(body.reference, "reference") + frames
            frames.sort(key=lambda f: (f["time"] or "", f["file"]))
        if len(frames) < 2:
            raise HTTPException(422, detail="A spot check needs at least 2 frames")
        if len(frames) > leaks.MAX_CHECK_FRAMES:
            raise HTTPException(422, detail=f"{len(frames)} frames: a spot check takes at most {leaks.MAX_CHECK_FRAMES}")
        check_id = uuid.uuid4().hex[:12]
        with lock:
            t = _test(test_id)
            t["checks"].append({
                "id": check_id, "comment": body.comment.strip(), "folder": str(_resolve(body.probe.folder)),
                "want": {k: getattr(body.probe, k) for k in WANT_KEYS},
                "referenceFolder": str(_resolve(body.reference.folder)) if body.reference else None,
                "frames": frames, "autoReference": False,
                "added": time.time(), "status": "queued", "result": None, "error": None,
            })
            cache.save_leak_test(test_id, t)
        _submit("checks", test_id, check_id)
        return _view(_test(test_id))

    @app.patch("/api/leaks/{test_id}/checks/{check_id}")
    def patch_check(test_id: str, check_id: str, body: CheckPatch) -> dict[str, Any]:
        """The check's title, and per frame: what the flashlight was on, and whether it is a reference."""
        with lock:
            t = _test(test_id)
            c = _item(t, "checks", check_id)
            if body.comment is not None:
                c["comment"] = body.comment.strip()
            for fp in body.frames:
                if fp.index >= len(c["frames"]):
                    raise HTTPException(422, detail=f"No frame {fp.index}")
                f = c["frames"][fp.index]
                if fp.role is not None and fp.role != f["role"]:
                    f["role"] = fp.role
                    c["autoReference"] = False
                if fp.comment is not None:
                    f["comment"] = fp.comment.strip()
            cache.save_leak_test(test_id, t)
        return _view(t)

    @app.delete("/api/leaks/{test_id}/checks/{check_id}")
    def delete_check(test_id: str, check_id: str) -> dict[str, Any]:
        """Drop a spot check (its arrays in var/leaks go too; the frames stay where they are)."""
        return _remove(test_id, "checks", check_id)

    @app.post("/api/leaks/{test_id}/checks/{check_id}/rerun")
    def rerun_check(test_id: str, check_id: str) -> dict[str, Any]:
        return _rerun(test_id, "checks", check_id)

    @app.get("/api/leaks/{test_id}/checks/{check_id}/frames/{index}/diff.png")
    def check_diff_png(test_id: str, check_id: str, index: int, scale: str = "shared", theme: str = "light"):
        """One frame minus the reference master, with its level: red is extra light."""
        c = _item(_test(test_id), "checks", check_id)
        if c["status"] != "done":
            raise HTTPException(409, detail="Not analysed yet")
        if not 0 <= index < len(c["frames"]):
            raise HTTPException(404, detail="No such frame")
        evaluation, extra = _evaluate(c)
        frame = extra["stack"][index]
        limit = evaluation["sharedLimit"] if scale == "shared" else evaluation["frames"][index]["limit"]
        return Response(leaks.render_diff(frame - extra["R"], limit, center=False, theme="dark" if theme == "dark" else "light"), **png)
