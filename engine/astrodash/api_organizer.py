"""Organizer endpoints (PLAN §3.2, §2.1): lint, dry-run plans, typed approval, apply, undo, trash.

A plan is a draft until you approve it by typing its id. Applying holds the
ingest lock, so auto-ingest never moves frames into a tree being reorganized.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .organizer import apply as applier
from .organizer.plan import TRASH, canonical_plan, new_revision_plan, new_target_plan


class PlanRequest(BaseModel):
    revisionId: str = Field(min_length=1, max_length=300)  # noqa: N815


class NewTargetRequest(BaseModel):
    catalogId: str = Field(min_length=1, max_length=60)  # noqa: N815
    name: str = Field(min_length=1, max_length=100)
    panels: int = Field(default=1, ge=1, le=50)


class ApplyRequest(BaseModel):
    # Approval: the plan id, typed by you in the UI.
    confirm: str = Field(min_length=1, max_length=100)


def _plan_id(slug: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^a-z0-9-]", "", slug.lower())[:40]


def register(app: FastAPI, state) -> None:
    cfg = state.cfg
    cache = state.cache
    root = Path(os.path.abspath(cfg.data_root))

    def _store(p, status: str = "draft") -> dict[str, Any]:
        pid = _plan_id(p.revision_id or p.target)
        data = p.as_dict()
        for op in data["ops"]:
            if op.get("dst"):
                op["dst"] = op["dst"].replace("{plan}", pid)
        if data["conflicts"]:
            status = "blocked"
        cache.save_plan(pid, p.revision_id, p.title, status, data)
        return cache.plan(pid)

    @app.get("/api/organizer")
    def organizer() -> dict[str, Any]:
        """Lint per revision: the dry-run canonical plan's findings and size (nothing stored)."""
        lib = state.require_library()
        rows = []
        for rev in sorted(lib.revisions.values(), key=lambda r: r["folder"].casefold()):
            p = canonical_plan(root, rev["id"], rev["folder"])
            d = p.as_dict()
            rows.append({
                "revisionId": rev["id"], "folder": rev["folder"], "catalogId": rev["catalogId"], "stage": rev["stage"]["value"],
                "lint": d["lint"], "counts": d["counts"], "warnings": d["warnings"], "conflicts": d["conflicts"],
                "canonical": not d["ops"],
            })
        return {"revisions": rows, "plans": [_summary(p) for p in cache.plans()], "trash": _trash(), "dataRoot": str(root)}

    def _summary(p: dict[str, Any]) -> dict[str, Any]:
        return {k: p[k] for k in ("id", "created", "revisionId", "title", "status", "counts", "result") if k in p}

    def _trash() -> dict[str, Any]:
        base = root / TRASH
        out = {"path": str(base), "plans": [], "bytes": 0, "files": 0}
        if not base.is_dir():
            return out
        for entry in sorted(base.iterdir()):
            size = files = 0
            for dirpath, _, filenames in os.walk(entry):
                for f in filenames:
                    try:
                        size += os.path.getsize(os.path.join(dirpath, f))
                        files += 1
                    except OSError:
                        pass
            out["plans"].append({"plan": entry.name, "bytes": size, "files": files})
            out["bytes"] += size
            out["files"] += files
        return out

    @app.post("/api/organizer/plans")
    def make_plan(body: PlanRequest) -> dict[str, Any]:
        lib = state.require_library()
        rev = lib.revisions.get(body.revisionId)
        if rev is None:
            raise HTTPException(404, detail="No such target revision")
        p = canonical_plan(root, rev["id"], rev["folder"])
        if not p.ops and not p.conflicts:
            raise HTTPException(409, detail=f"{rev['folder']} already follows the canonical layout")
        return _store(p)

    @app.post("/api/organizer/new-revision")
    def new_revision(body: PlanRequest) -> dict[str, Any]:
        lib = state.require_library()
        rev = lib.revisions.get(body.revisionId)
        if rev is None:
            raise HTTPException(404, detail="No such target revision")
        siblings = [r for r in lib.revisions.values() if r["groupId"] == rev["groupId"]]
        n = max(r["revision"] for r in siblings) + 1
        base = re.sub(r"\s+R\d+$", "", rev["folder"])
        text = json.dumps({"catalog_id": rev["catalogId"], "name": rev["name"], "revision": n, "status": "acquiring", "notes": ""}, indent=2) + "\n"
        return _store(new_revision_plan(root, rev["id"], base, rev["catalogId"], rev["name"], n, text))

    @app.post("/api/organizer/new-target")
    def new_target(body: NewTargetRequest) -> dict[str, Any]:
        """Revision 1 of a target the library does not have yet. A catalog id it has needs New revision instead."""
        lib = state.require_library()
        catalog_id, name = (" ".join(v.split()) for v in (body.catalogId, body.name))
        known = sorted(r["folder"] for r in lib.revisions.values() if r["catalogId"].casefold() == catalog_id.casefold())
        if known:
            raise HTTPException(409, detail=f"{catalog_id} is already in the library ({', '.join(known)}); use New revision on it instead")
        meta: dict[str, Any] = {"catalog_id": catalog_id, "name": name, "revision": 1, "status": "acquiring"}
        if body.panels > 1:
            meta["mosaic"] = {"panels": body.panels}
        meta["notes"] = ""
        return _store(new_target_plan(root, catalog_id, name, json.dumps(meta, indent=2) + "\n"))

    @app.get("/api/organizer/plans/{plan_id}")
    def get_plan(plan_id: str) -> dict[str, Any]:
        p = cache.plan(plan_id)
        if p is None:
            raise HTTPException(404, detail="No such plan")
        return {**p, "dataRoot": str(root)}

    @app.post("/api/organizer/plans/{plan_id}/apply")
    def apply(plan_id: str, body: ApplyRequest) -> dict[str, Any]:
        p = cache.plan(plan_id)
        if p is None:
            raise HTTPException(404, detail="No such plan")
        if body.confirm.strip() != plan_id:
            raise HTTPException(422, detail="Type the plan id exactly to approve it")
        if p["status"] != "draft":
            raise HTTPException(409, detail=f"This plan is {p['status']}; only a draft can be applied")
        # Re-check every first-wave operation before touching anything; the rest are checked as they run.
        lock = state.ingest._lock if getattr(state, "ingest", None) else None
        if lock:
            lock.acquire()
        try:
            cache.save_plan(plan_id, p["revisionId"], p["title"], "applying", _data(p))
            result = applier.apply_plan(root, plan_id, p["ops"])
        finally:
            if lock:
                lock.release()
        status = "applied" if result["error"] is None else ("partial" if result["done"] else "failed")
        _remap(p, result["done"])
        cache.save_plan(plan_id, p["revisionId"], p["title"], status, _data(p), result)
        state.start_scan()
        return cache.plan(plan_id)

    def _remap(p: dict[str, Any], done: list[int]) -> None:
        """Keep stored diagnostics pointing at the frames after they moved (and at renamed sessions)."""
        if not p.get("revisionId"):
            return
        moves = [(p["ops"][i]["src"], p["ops"][i]["dst"]) for i in done if p["ops"][i]["op"] in ("move", "trash")]
        renames = {}
        for src, dst in moves:
            a, b = src.rsplit("/", 1), dst.rsplit("/", 1)
            if len(a) == 2 and len(b) == 2 and a[0].endswith(".SessionData") and b[0].endswith(".SessionData") and a[1] != b[1]:
                renames[a[1]] = b[1]
        if moves:
            cache.remap_diagnostics(p["revisionId"], moves, renames)

    @app.post("/api/organizer/plans/{plan_id}/undo")
    def undo(plan_id: str) -> dict[str, Any]:
        p = cache.plan(plan_id)
        if p is None:
            raise HTTPException(404, detail="No such plan")
        done = (p.get("result") or {}).get("done") or []
        if not done:
            raise HTTPException(409, detail="Nothing of this plan was applied")
        pid = _plan_id("undo-" + plan_id)
        ops = applier.inverse(p["ops"], done)
        for op in ops:
            if op.get("dst"):
                op["dst"] = op["dst"].replace("{plan}", pid)
        data = {"revisionId": p["revisionId"], "target": p.get("target"), "title": f"Undo {plan_id}", "ops": ops,
                "counts": _counts(ops), "warnings": [], "conflicts": [], "lint": [], "undoOf": plan_id}
        cache.save_plan(pid, p["revisionId"], data["title"], "draft", data)
        return cache.plan(pid)

    @app.post("/api/organizer/plans/{plan_id}/discard")
    def discard(plan_id: str) -> dict[str, Any]:
        """Drop a draft you will not apply (a record in the dashboard database; no files involved)."""
        p = cache.plan(plan_id)
        if p is None:
            raise HTTPException(404, detail="No such plan")
        if p["status"] not in ("draft", "blocked"):
            raise HTTPException(409, detail="Only a draft can be discarded")
        cache.save_plan(plan_id, p["revisionId"], p["title"], "discarded", _data(p))
        return cache.plan(plan_id)

    @app.get("/api/organizer/trash")
    def trash() -> dict[str, Any]:
        return _trash()


def _data(p: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in p.items() if k not in ("id", "created", "revisionId", "title", "status", "result")}


def _counts(ops: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in ops:
        out[o["op"]] = out.get(o["op"], 0) + 1
    return out
