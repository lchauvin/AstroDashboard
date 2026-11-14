"""Quarantine a night's rejected lights, and put them back (PLAN §5.2 "Quarantine selected").

Read-only, like plan.py: these build organizer plans; `apply.py` runs them once you approve.

A rejected light moves from `.SessionData` to the revision's quarantine folder at the same
relative path (`.SessionData/NIGHT_…/LIGHT/H/x.fits` → `.MispointedFrames_Quarantine/NIGHT_…/LIGHT/H/x.fits`).
WBPP is pointed at `.SessionData`, so it never sees them; the Library counts them as quarantined.

The restore puts back only what a quarantine plan moved: the quarantine folder may also hold
frames set aside by hand (mispointed ones), and a re-run of the analysis no longer lists the
quarantined frames. So the restore is built from the applied quarantine plans, not from the
folder or the stored result.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..layout import COPY_DIRS, QUARANTINE_PREFIX, REJECTED_DIRS, SESSION_DATA
from .plan import Plan, Planner

KIND_QUARANTINE = "quarantine"
KIND_RESTORE = "restore-quarantine"


def _split(path: str, target: str, session: str) -> tuple[list[str], int] | None:
    """The path's parts and the index of its `.SessionData`, when it is a file of this night there."""
    parts = path.split("/")
    if SESSION_DATA not in parts[:-1]:
        return None
    i = parts.index(SESSION_DATA)
    ok = parts[0] == target and len(parts) > i + 2 and parts[i + 1] == session
    return (parts, i) if ok else None


def rejected_lights(result: dict[str, Any], target: str, session: str) -> list[str]:
    """Rejected lights (astro-eval's verdict with your overrides applied) still in `.SessionData`."""
    out = []
    for g in result.get("lights", []):
        for f in g.get("frames", []):
            if f.get("rejected") and _split(f["path"], target, session):
                out.append(f["path"])
    return out


def quarantined_lights(result: dict[str, Any]) -> set[str]:
    """Lights of a stored result that sit in a quarantine folder."""
    return {f["path"] for g in result.get("lights", []) for f in g.get("frames", []) if in_quarantine(f["path"])}


def in_quarantine(path: str) -> bool:
    return any(p.startswith(QUARANTINE_PREFIX) for p in path.split("/")[:-1])


def _quarantine_dir(tree, parent: str) -> str:
    """The revision's existing quarantine folder, or the default name."""
    existing = [c for c in tree.children(parent) if c.startswith(QUARANTINE_PREFIX) and tree.is_dir(f"{parent}/{c}")]
    return existing[0] if existing else QUARANTINE_PREFIX


def _quarantine_moves(plans: list[dict[str, Any]], revision_id: str, session: str) -> dict[str, str]:
    """quarantined path → original path, for every light the applied quarantine plans of this night moved."""
    out: dict[str, str] = {}
    for p in sorted(plans, key=lambda p: p["created"]):
        if p.get("kind") != KIND_QUARANTINE or p.get("revisionId") != revision_id or p.get("session") != session:
            continue
        if p["status"] not in ("applied", "partial"):
            continue
        for i in (p.get("result") or {}).get("done") or []:
            op = p["ops"][i]
            if op["op"] == "move" and op.get("kind") == "file":
                out[op["dst"]] = op["src"]
    return out


def _identical(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(1 << 20), fb.read(1 << 20)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def _looks_same(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 2.0


def duplicates(plans: list[dict[str, Any]], root: Path, revision_id: str, session: str, exact: bool = True) -> list[str]:
    """Lights back in .SessionData although quarantined: a byte-identical copy refiled at the freed path
    (a re-synced download ingested before ingest knew about the quarantine). WBPP would stack them.
    `exact=False` compares size and timestamp only (a count for a page); a plan always compares the bytes."""
    same = _identical if exact else _looks_same
    return sorted(o for q, o in _quarantine_moves(plans, revision_id, session).items()
                  if os.path.isfile(root / o) and same(root / o, root / q))


def quarantine_plan(root: Path, revision_id: str, target: str, session: str, paths: list[str],
                    copies_of_quarantined: list[str] | None = None) -> Plan:
    n = len(paths) + len(copies_of_quarantined or [])
    pl = Planner(root, revision_id, target, f"Quarantine {n} rejected light{'s' if n != 1 else ''} of {session}")
    plan = pl.plan
    tree = pl.tree
    for path in copies_of_quarantined or []:
        if path in tree.files:
            pl.trash(path, "{plan}", "identical copy of a light already in the quarantine (refiled by ingest)")
    for path in paths:
        split = _split(path, target, session)
        if split is None:
            plan.conflicts.append(f"{path} is not a light of {session} in {SESSION_DATA}")
            continue
        if path not in tree.files:
            plan.warnings.append(f"{path} is no longer on disk; skipped.")
            continue
        parts, i = split
        parent = "/".join(parts[:i])
        dst = "/".join([parent, _quarantine_dir(tree, parent), *parts[i + 1:]])
        pl.move(path, dst, "rejected by the analysis: out of WBPP's reach")

    # astro-eval's own copies (_REJECTED / Accepted) stay in .SessionData, where WBPP would load them.
    copies: set[str] = set()
    for f in tree.files:
        split = _split(f, target, session)
        if split is None:
            continue
        parts, i = split
        for k in range(i + 2, len(parts) - 1):
            if parts[k].casefold() in REJECTED_DIRS | COPY_DIRS:
                copies.add("/".join(parts[:k + 1]))
                break
    if copies:
        plan.warnings.append(
            "astro-eval copies are still in this night and are not moved: WBPP's Add Directory would load them too. "
            "Remove them or leave them out of WBPP: " + ", ".join(sorted(copies)))
    plan.extra = {"kind": KIND_QUARANTINE, "session": session}
    plan.lint.append({"code": "quarantine", "level": "info", "text": f"{sum(o.op == 'move' for o in plan.ops)} lights to the quarantine"})
    if copies_of_quarantined:
        plan.lint.append({"code": "quarantine-copies", "level": "info",
                          "text": f"{len(copies_of_quarantined)} copies of quarantined lights to .Trash"})
    return plan


def restorable(plans: list[dict[str, Any]], root: Path, revision_id: str, session: str) -> list[tuple[str, str]]:
    """(quarantined path, original path) for what applied quarantine plans of this night moved and is still there."""
    out = _quarantine_moves(plans, revision_id, session)
    return [(q, o) for q, o in out.items() if os.path.isfile(root / q) and not os.path.exists(root / o)]


def restore_plan(root: Path, revision_id: str, target: str, session: str, moves: list[tuple[str, str]]) -> Plan:
    pl = Planner(root, revision_id, target, f"Put back {len(moves)} quarantined light{'s' if len(moves) != 1 else ''} of {session}")
    emptied: set[str] = set()
    for q, original in moves:
        if q not in pl.tree.files:
            pl.plan.warnings.append(f"{q} is no longer on disk; skipped.")
            continue
        if pl.move(q, original, "back to the folder it was quarantined from"):
            parts = q.split("/")
            area = next(i for i, p in enumerate(parts) if p.startswith(QUARANTINE_PREFIX))
            emptied.update("/".join(parts[:k]) for k in range(area + 1, len(parts)))
    # Folders the quarantine emptied, deepest first; a folder that still holds anything stays.
    for d in sorted(emptied, key=lambda d: d.count("/"), reverse=True):
        pl.rmdir_empty(d, "emptied by the restore")
    pl.plan.extra = {"kind": KIND_RESTORE, "session": session}
    return pl.plan
