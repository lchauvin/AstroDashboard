"""Organizer plans: the canonical-layout migration of one revision, as a list of operations (PLAN §2.1, §3.2).

Read-only. A plan is generated against a *virtual* copy of the target's tree,
so every operation sees the effect of the ones before it: a merge into a
folder that an earlier step created goes file by file, and a clash is caught
here, before anything runs. `apply.py` executes an approved plan; nothing
else ever does.

Operations (no permanent delete exists in this vocabulary):
  move(src, dst)        rename a file or folder (same volume, never onto an existing path)
  mkdir(dst)            create a folder
  rmdir_empty(dst)      remove a folder that is empty at that point
  trash(src, dst)       move into .Trash\\<plan-id>\\<original relative path>
  write_new(dst, text)  create a file that must not exist (target.json of a new revision)
  repoint(src, dst)     re-point the paths inside a PixInsight project.xosm (pxi.py); the
                        original is moved to dst (.Dashboard/backups/<plan-id>/…), never lost

All paths are relative to the data root, with "/" separators.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import pxi
from ..layout import (
    LEGACY_PANEL_RE,
    PANEL_RE,
    QUARANTINE_PREFIX,
    SESSION_CANONICAL_RE,
    SESSION_DATA,
    SESSION_EXCLUDED_RE,
    SESSION_LEGACY_RE,
    YEAR_RE,
)

LEGACY_STAGE_RE = re.compile(r"^[1-7]\.\s")
TYPE_FOLDERS = {"LIGHT", "FLAT", "DARK", "BIAS", "DARKFLAT", "FLATDARK"}
KEEP_AT_NIGHT = {"snapshot", "snapshots"}
TRASH = ".Trash"


@dataclass
class Op:
    op: str
    src: str | None = None
    dst: str | None = None
    kind: str = "dir"              # "dir" | "file"
    size: int | None = None        # files: re-checked before applying
    mtime: float | None = None
    text: str | None = None        # write_new
    sha1: str | None = None        # repoint: the project file as planned
    refs: int | None = None        # repoint: references that change
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


@dataclass
class Plan:
    revision_id: str | None
    target: str
    title: str
    ops: list[Op] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    lint: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for o in self.ops:
            counts[o.op] = counts.get(o.op, 0) + 1
        return {
            "revisionId": self.revision_id,
            "target": self.target,
            "title": self.title,
            "ops": [o.as_dict() for o in self.ops],
            "counts": counts,
            "warnings": self.warnings,
            "conflicts": self.conflicts,
            "lint": self.lint,
        }


class VirtualTree:
    """Folders and files below one target folder, mutated as operations are planned."""

    def __init__(self, root: Path, top: str):
        self.root = root
        self.dirs: set[str] = set()
        self.files: dict[str, tuple[int, float]] = {}
        base = root / top
        self.dirs.add(top)
        for dirpath, dirnames, filenames in os.walk(base):
            rel = Path(dirpath).relative_to(root).as_posix()
            self.dirs.add(rel)
            for f in filenames:
                try:
                    st = os.stat(os.path.join(dirpath, f))
                except OSError:
                    continue
                self.files[f"{rel}/{f}"] = (st.st_size, st.st_mtime)

    def exists(self, p: str) -> bool:
        return p in self.dirs or p in self.files

    def is_dir(self, p: str) -> bool:
        return p in self.dirs

    def children(self, p: str) -> list[str]:
        pre = p + "/"
        out = {x[len(pre):].split("/", 1)[0] for x in (*self.dirs, *self.files) if x.startswith(pre)}
        return sorted(out, key=str.casefold)

    def has_files(self, p: str) -> bool:
        pre = p + "/"
        return any(f.startswith(pre) for f in self.files)

    def move(self, src: str, dst: str) -> None:
        if src in self.files:
            self.files[dst] = self.files.pop(src)
            return
        pre = src + "/"
        self.dirs = {dst + d[len(src):] if d == src or d.startswith(pre) else d for d in self.dirs}
        self.files = {(dst + f[len(src):] if f.startswith(pre) else f): v for f, v in self.files.items()}

    def mkdirs(self, p: str) -> list[str]:
        made = []
        parts = p.split("/")
        for i in range(1, len(parts) + 1):
            q = "/".join(parts[:i])
            if q not in self.dirs:
                self.dirs.add(q)
                made.append(q)
        return made

    def forget_dir(self, p: str) -> None:
        """Virtual only: the folder no longer exists in the planned tree."""
        self.dirs.discard(p)


class Planner:
    def __init__(self, root: Path, revision_id: str, target: str, title: str):
        self.root = root
        self.tree = VirtualTree(root, target)
        self.plan = Plan(revision_id, target, title)

    # -------------------------------------------------------------- primitives

    def _ensure_parent(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0]
        for d in self.tree.mkdirs(parent):
            self.plan.ops.append(Op("mkdir", dst=d, why="new folder"))

    def move(self, src: str, dst: str, why: str) -> bool:
        if self.tree.exists(dst):
            self.plan.conflicts.append(f"{dst} already exists (from {src})")
            return False
        self._ensure_parent(dst)
        info = self.tree.files.get(src)
        self.plan.ops.append(Op("move", src, dst, "file" if info else "dir", info[0] if info else None, info[1] if info else None, why=why))
        self.tree.move(src, dst)
        return True

    def merge(self, src: str, dst: str, why: str) -> None:
        """Move src into dst: a single rename when dst is free, file by file when it exists."""
        if not self.tree.exists(dst):
            self.move(src, dst, why)
            return
        if not (self.tree.is_dir(src) and self.tree.is_dir(dst)):
            self.plan.conflicts.append(f"{dst} already exists (from {src})")
            return
        for child in self.tree.children(src):
            self.merge(f"{src}/{child}", f"{dst}/{child}", why)
        self.rmdir_empty(src, "emptied by the merge")

    def rmdir_empty(self, path: str, why: str) -> None:
        if not self.tree.is_dir(path) or self.tree.has_files(path):
            return
        for child in self.tree.children(path):  # nested empty folders first
            self.rmdir_empty(f"{path}/{child}", why)
        if not self.tree.children(path):
            self.plan.ops.append(Op("rmdir_empty", dst=path, why=why))
            self.tree.forget_dir(path)

    def trash(self, src: str, plan_id_placeholder: str, why: str) -> None:
        dst = f"{TRASH}/{plan_id_placeholder}/{src}"
        info = self.tree.files.get(src)
        self.plan.ops.append(Op("trash", src, dst, "file" if info else "dir", why=why))
        self.tree.move(src, dst)

    # -------------------------------------------------------------- the migration

    def canonical(self) -> Plan:
        t = self.plan.target
        tree = self.tree
        lint = self.plan.lint
        projects = sorted(f for f in tree.files if f.endswith(f".pxiproject/{pxi.XOSM}"))

        years = [c for c in tree.children(t) if YEAR_RE.match(c) and tree.is_dir(f"{t}/{c}")]
        if len(years) > 1:
            self.plan.warnings.append(
                f"Several year folders ({', '.join(years)}): each should become its own revision. Not flattened automatically.")
            lint.append({"code": "years", "level": "warn", "text": f"{len(years)} year folders"})
        elif years:
            y = years[0]
            lint.append({"code": "year", "level": "warn", "text": f"Year level {y}\\ (canonical: none)"})
            for child in tree.children(f"{t}/{y}"):
                self.merge(f"{t}/{y}/{child}", f"{t}/{child}", f"flatten the {y} year level")
            self.rmdir_empty(f"{t}/{y}", f"year level {y} now empty")

        for area in [c for c in tree.children(t) if c == SESSION_DATA or c.startswith(QUARANTINE_PREFIX)]:
            base = f"{t}/{area}"
            for session in tree.children(base):
                path = f"{base}/{session}"
                if not tree.is_dir(path):
                    continue
                if SESSION_EXCLUDED_RE.match(session):
                    lint.append({"code": "flagged", "level": "warn", "text": f"Legacy flagged session {session}"})
                    self.trash(path, "{plan}", "legacy flagged session (decided 2026-10-08: remove)")
                    continue
                m = SESSION_CANONICAL_RE.match(session) or SESSION_LEGACY_RE.match(session)
                if not m:
                    lint.append({"code": "nonconforming", "level": "warn", "text": f"Session folder {session} is not NIGHT_<date>; left alone"})
                    continue
                night = f"{base}/NIGHT_{m[1]}"
                if session != f"NIGHT_{m[1]}":
                    lint.append({"code": "session-name", "level": "info", "text": f"{session} → NIGHT_{m[1]}"})
                    self.merge(path, night, "session folder named NIGHT_<date>")
                self._night(night)

        for c in tree.children(t):
            if LEGACY_STAGE_RE.match(c) and tree.is_dir(f"{t}/{c}") and not tree.has_files(f"{t}/{c}"):
                lint.append({"code": "empty-stage", "level": "info", "text": f"Empty {c}"})
                self.rmdir_empty(f"{t}/{c}", "empty legacy processing folder")

        self._repoint(projects)
        return self.plan

    def _repoint(self, projects: list[str]) -> None:
        """Last steps: PixInsight projects follow the moves (and leave a former data root such as E:/Photo/Astro)."""
        root = pxi.posix_root(self.root)
        moves = [(o.src, o.dst) for o in self.plan.ops if o.op == "move"]
        trashed = [o.src for o in self.plan.ops if o.op == "trash"]
        mapper = pxi.Mapper(root, pxi.top_level(self.root), moves, trashed)
        edited = []
        for rel in projects:
            bundle = rel.rsplit("/", 2)[-2]
            if mapper.trashed_ref(f"{root}/{rel}"):
                continue  # inside a session going to the trash: it travels with it
            try:
                sha1, refs = pxi.scan(self.root / rel)
                st = (self.root / rel).stat()
            except OSError as exc:
                self.plan.warnings.append(f"{bundle} could not be read ({exc}); its paths are not re-pointed.")
                continue
            stats = pxi.plan_refs(refs, mapper)
            n = stats["moved"] + stats["formerRoot"]
            if stats["trashed"]:
                self.plan.warnings.append(
                    f"{bundle}: {stats['trashed']} references into sessions going to the trash stay as they are.")
            if not n:
                continue
            now = (mapper(f"{root}/{rel}") or f"{root}/{rel}")[len(root) + 1:]
            self.plan.ops.append(Op(
                "repoint", now, f".Dashboard/backups/{{plan}}/{now}", "file", st.st_size, st.st_mtime,
                sha1=sha1, refs=n, why="PixInsight projects follow the new paths"))
            parts = []
            if stats["moved"]:
                n_moved = stats["moved"]
                parts.append(f"{n_moved:,} path{'s follow' if n_moved > 1 else ' follows'} the moves")
            if stats["formerRoot"]:
                parts.append(f"{stats['formerRoot']:,} leave a former data root for {root}")
            self.plan.lint.append({"code": "pxiproject", "level": "info", "text": f"{bundle}: " + ", ".join(parts)})
            edited.append(bundle)
        if edited:
            self.plan.warnings.append(
                f"PixInsight will say the seal of {', '.join(edited)} has been edited: expected. Open the project, "
                "check it, and save it once to reseal it. The original project file is kept in "
                ".Dashboard\\backups until you undo or remove it.")

    def _night(self, night: str) -> None:
        tree = self.tree
        for child in tree.children(night):
            path = f"{night}/{child}"
            if not tree.is_dir(path) or child.upper() in TYPE_FOLDERS or PANEL_RE.match(child) or child.casefold() in KEEP_AT_NIGHT:
                continue
            if child.casefold() == "flatwizard":
                self.plan.lint.append({"code": "flatwizard", "level": "info", "text": f"{night.rsplit('/', 1)[1]}: FlatWizard\\FLAT → FLAT"})
                for sub in tree.children(path):
                    self.merge(f"{path}/{sub}", f"{night}/{sub}", "FlatWizard\\FLAT → FLAT")
                self.rmdir_empty(path, "FlatWizard emptied")
                continue
            m = LEGACY_PANEL_RE.search(child)
            if m:
                self.plan.lint.append({"code": "panel", "level": "info", "text": f"{night.rsplit('/', 1)[1]}: {child} → PANEL_{m[1]}"})
                self.merge(path, f"{night}/PANEL_{m[1]}", "mosaic panel folder named PANEL_<n>")
                continue
            # A NINA object folder above LIGHT/FLAT: its contents move up to the night.
            if any(c.upper() in TYPE_FOLDERS for c in tree.children(path)):
                self.plan.lint.append({"code": "object-folder", "level": "info", "text": f"{night.rsplit('/', 1)[1]}: {child}\\LIGHT → LIGHT"})
                for sub in tree.children(path):
                    self.merge(f"{path}/{sub}", f"{night}/{sub}", f"NINA object folder {child} flattened")
                self.rmdir_empty(path, "object folder emptied")


def canonical_plan(root: Path, revision_id: str, target: str) -> Plan:
    return Planner(root, revision_id, target, f"Canonical layout for {target}").canonical()


def new_revision_plan(root: Path, revision_id: str, base_folder: str, catalog_id: str, name: str, revision: int, text: str) -> Plan:
    folder = f"{base_folder} R{revision}"
    plan = Plan(revision_id, folder, f"Start revision {revision} of {catalog_id}")
    if (root / folder).exists():
        plan.conflicts.append(f"{folder} already exists")
        return plan
    plan.ops += [
        Op("mkdir", dst=folder, why="new revision folder"),
        Op("mkdir", dst=f"{folder}/{SESSION_DATA}", why="where its nights will go"),
        Op("write_new", dst=f"{folder}/target.json", kind="file", text=text, why="revision metadata"),
    ]
    return plan


# Characters a target folder name cannot hold: Windows-reserved, plus the parentheses that delimit the
# common name and the brackets that are wildcards in PowerShell paths (PLAN §3.2).
_BAD_NAME_RE = re.compile(r'[<>:"/\|?*()\[\]\x00-\x1f]')


def target_name_problems(catalog_id: str, name: str) -> list[str]:
    out = []
    for label, value in (("Catalog id", catalog_id), ("Name", name)):
        if not value:
            out.append(f"{label} is empty")
        elif _BAD_NAME_RE.search(value):
            out.append(f'{label} cannot contain < > : " / \\ | ? * ( ) [ ]')
        elif value != value.strip() or value.endswith("."):
            out.append(f"{label} cannot start or end with a space, or end with a dot")
    if re.search(r"\sR\d+$", name):
        out.append('Name cannot end in " R<n>": that suffix marks a revision')
    return out


def new_target_plan(root: Path, catalog_id: str, name: str, text: str) -> Plan:
    """Revision 1 of a new target: the canonical skeleton and its target.json. Creates only; moves nothing."""
    folder = f"{catalog_id} ({name})"
    plan = Plan(None, folder, f"Create target {folder}")
    plan.conflicts += target_name_problems(catalog_id, name)
    if not plan.conflicts and (root / folder).exists():
        plan.conflicts.append(f"{folder} already exists")
    if plan.conflicts:
        return plan
    plan.ops += [
        Op("mkdir", dst=folder, why="new target folder"),
        Op("mkdir", dst=f"{folder}/{SESSION_DATA}", why="where its nights will go"),
        Op("mkdir", dst=f"{folder}/.Calibration", why="master flats for this target"),
        Op("mkdir", dst=f"{folder}/.Finished", why="finished images"),
        Op("write_new", dst=f"{folder}/target.json", kind="file", text=text, why="revision metadata"),
    ]
    return plan
