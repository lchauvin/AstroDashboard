"""Applies an approved organizer plan (PLAN §2.1). The only organizer code that changes the data root.

Every operation is re-checked against the disk just before it runs:
- the source exists (and a file still has the size and mtime recorded when the plan was made);
- the destination does not exist — nothing is ever overwritten;
- every path stays inside the data root, out of `.Download` (ingest's lane) and
  out of the global `.Calibration` library; `trash` only targets `.Trash\\<plan>`;
- renames stay on one volume (atomic).

There is no delete: `rmdir_empty` uses `os.rmdir`, which refuses a folder with
anything in it, and `trash` is a move. The first failed check stops the plan;
what already ran is journaled and can be undone with the inverse plan.
Each operation is appended to `<root>\\.Dashboard\\journal.jsonl` as it runs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

FORBIDDEN_TOPS = {".Download", ".Calibration"}
TRASH = ".Trash"
JOURNAL_DIR = ".Dashboard"


class PlanRefused(Exception):
    pass


def _abs(root: Path, rel: str) -> Path:
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts or ":" in rel:
        raise PlanRefused(f"not a relative path inside the data root: {rel!r}")
    p = Path(os.path.abspath(root / rel))
    try:
        p.relative_to(root)
    except ValueError as exc:
        raise PlanRefused(f"outside the data root: {rel}") from exc
    if p.parts[len(root.parts):][:1] and p.parts[len(root.parts)] in FORBIDDEN_TOPS:
        raise PlanRefused(f"{p.parts[len(root.parts)]} is not the organizer's to change: {rel}")
    return p


def check(root: Path, op: dict[str, Any], plan_id: str) -> tuple[Path | None, Path | None]:
    """Validate one operation against the disk. Raises PlanRefused with the reason."""
    kind = op["op"]
    src = _abs(root, op["src"]) if op.get("src") else None
    dst = _abs(root, op["dst"]) if op.get("dst") else None
    if kind in ("move", "trash"):
        if src is None or dst is None:
            raise PlanRefused(f"{kind} needs src and dst")
        if not src.exists():
            raise PlanRefused(f"source is gone: {op['src']}")
        if op.get("kind") == "file":
            st = src.stat()
            if op.get("size") is not None and (st.st_size != op["size"] or abs(st.st_mtime - op["mtime"]) > 2):
                raise PlanRefused(f"source changed since the plan was made: {op['src']}")
        if dst.exists():
            raise PlanRefused(f"destination exists (never overwritten): {op['dst']}")
        if kind == "trash" and not op["dst"].startswith(f"{TRASH}/{plan_id}/"):
            raise PlanRefused(f"trash must go to {TRASH}/{plan_id}/: {op['dst']}")
        if os.path.splitdrive(str(src))[0].lower() != os.path.splitdrive(str(dst))[0].lower():
            raise PlanRefused("cross-volume move")
    elif kind == "mkdir":
        if dst is None or (dst.exists() and not dst.is_dir()):
            raise PlanRefused(f"cannot create folder: {op.get('dst')}")
    elif kind == "rmdir_empty":
        if dst is None or not dst.is_dir():
            raise PlanRefused(f"not a folder: {op.get('dst')}")
        if any(dst.iterdir()):
            raise PlanRefused(f"folder is not empty (never removed): {op['dst']}")
    elif kind == "write_new":
        if dst is None or dst.exists():
            raise PlanRefused(f"file exists (never overwritten): {op.get('dst')}")
    else:
        raise PlanRefused(f"unknown operation {kind!r}")
    return src, dst


def _journal(root: Path, entry: dict[str, Any]) -> None:
    d = root / JOURNAL_DIR
    d.mkdir(exist_ok=True)
    with open(d / "journal.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def apply_plan(root: Path, plan_id: str, ops: list[dict[str, Any]], on_op: Callable[[int, dict], None] | None = None) -> dict[str, Any]:
    """Run the operations in order; stop at the first refusal or error. Returns {done, error}."""
    root = Path(os.path.abspath(root))
    # Check everything that can be checked up front (sources of the first wave), then each op as it runs.
    done: list[int] = []
    for i, op in enumerate(ops):
        try:
            src, dst = check(root, op, plan_id)
            kind = op["op"]
            if kind == "move":
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
            elif kind == "trash":
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
            elif kind == "mkdir":
                dst.mkdir(parents=True, exist_ok=True)
            elif kind == "rmdir_empty":
                os.rmdir(dst)
            elif kind == "write_new":
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "x", encoding="utf-8") as fh:
                    fh.write(op.get("text") or "")
        except Exception as exc:  # noqa: BLE001 — stop here; what ran is journaled
            _journal(root, {"ts": time.time(), "plan": plan_id, "index": i, "op": op["op"], "src": op.get("src"), "dst": op.get("dst"), "error": f"{type(exc).__name__}: {exc}"})
            return {"done": done, "error": f"Step {i + 1} of {len(ops)} ({op['op']} {op.get('src') or op.get('dst')}): {exc}"}
        _journal(root, {"ts": time.time(), "plan": plan_id, "index": i, "op": op["op"], "src": op.get("src"), "dst": op.get("dst")})
        done.append(i)
        if on_op:
            on_op(i, op)
    return {"done": done, "error": None}


def inverse(ops: list[dict[str, Any]], done: list[int]) -> list[dict[str, Any]]:
    """The undo of what actually ran, newest first. write_new is undone by moving the file to the trash."""
    out: list[dict[str, Any]] = []
    for i in sorted(done, reverse=True):
        op = ops[i]
        k = op["op"]
        if k in ("move", "trash"):
            out.append({"op": "move", "src": op["dst"], "dst": op["src"], "kind": op.get("kind", "dir"), "why": f"undo: {op.get('why', k)}"})
        elif k == "mkdir":
            out.append({"op": "rmdir_empty", "dst": op["dst"], "why": "undo: folder created by the plan"})
        elif k == "rmdir_empty":
            out.append({"op": "mkdir", "dst": op["dst"], "why": "undo: folder removed by the plan"})
        elif k == "write_new":
            out.append({"op": "trash", "src": op["dst"], "dst": f"{TRASH}/{{plan}}/{op['dst']}", "kind": "file", "why": "undo: file written by the plan"})
    return out
