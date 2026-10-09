"""The auto-ingest service: a background thread that plans, and in Auto mode moves (PLAN §5.1).

Modes (stored in the dashboard database, default "dry-run"):
  off      — nothing is scanned;
  dry-run  — the plan is computed and shown, nothing moves;
  auto     — ready actions are applied through `fileops`, journaled first-to-last.

Moves are the only automatic change. Undo is per `.Download` night and moves
files back; copies of shared flats are reported (removing them would be a
deletion, which needs you). Empty `.Download` folders are removed only when you
press the button for that night.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import fileops
from .planner import DOWNLOAD, NIGHT_DIR_RE, Plan, TargetRef, plan_ingest, split_panel

log = logging.getLogger(__name__)

MODES = ("off", "dry-run", "auto")


class IngestService:
    def __init__(self, cfg, cache, library: Callable[[], Any], on_moved: Callable[[], None],
                 sequence_running: Callable[[], bool | None] = lambda: None, interval_s: float = 15.0,
                 stable_s: float = 30.0):
        self.cfg = cfg
        self.cache = cache
        self._library = library
        self._on_moved = on_moved
        self._sequence_running = sequence_running
        self.interval_s = interval_s
        self.stable_s = stable_s
        self.last_plan: Plan | None = None
        self.last_run: dict[str, Any] | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ state

    @property
    def mode(self) -> str:
        m = (self.cache.get_json("ingest") or {}).get("mode", "dry-run")
        return m if m in MODES else "dry-run"

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.cache.set_json("ingest", {**(self.cache.get_json("ingest") or {}), "mode": mode, "modeChanged": time.time()})
        self._wake.set()

    def closed_nights(self) -> set[str]:
        """Nights you closed, plus every night whose next local noon has passed, plus all when the sequence has ended."""
        closed = set((self.cache.get_json("ingest") or {}).get("closed", []))
        base = self.cfg.data_root / DOWNLOAD
        running = self._sequence_running()
        if base.is_dir():
            now = datetime.now()
            for p in base.iterdir():
                m = NIGHT_DIR_RE.match(p.name) if p.is_dir() else None
                if not m:
                    continue
                noon_after = datetime.combine(date.fromisoformat(m[1]) + timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
                if now >= noon_after or running is False:
                    closed.add(p.name)
        return closed

    def paused_nights(self) -> set[str]:
        """Nights you undid: Auto leaves them alone until you resume them (otherwise undo would be re-done at once)."""
        return set((self.cache.get_json("ingest") or {}).get("paused", []))

    def _set_paused(self, download_night: str, paused: bool) -> None:
        st = self.cache.get_json("ingest") or {}
        current = set(st.get("paused", []))
        st["paused"] = sorted(current | {download_night} if paused else current - {download_night})
        self.cache.set_json("ingest", st)

    def resume_night(self, download_night: str) -> None:
        self._set_paused(download_night, False)
        self._wake.set()

    def close_night(self, download_night: str) -> None:
        st = self.cache.get_json("ingest") or {}
        st["closed"] = sorted(set(st.get("closed", [])) | {download_night})
        self.cache.set_json("ingest", st)
        self._wake.set()

    # ------------------------------------------------------------------ mapping

    def learned_map(self, library) -> dict[str, dict[str, Any]]:
        """NINA OBJECT → catalog target, learned from the frames already in the library."""
        votes: dict[str, Counter] = defaultdict(Counter)
        display: dict[str, str] = {}
        rev_of = {r["folder"]: r for r in library.revisions.values()}
        for f in library.ok_lights:
            obj, _ = split_panel(f.header.get("OBJECT"))
            rev = rev_of.get(f.target_folder or "")
            if obj and rev:
                votes[obj.casefold()][rev["catalogId"]] += 1
                display[obj.casefold()] = obj
        return {k: {"object": display[k], "catalogId": c.most_common(1)[0][0], "revisionId": None, "learned": True, "frames": sum(c.values())}
                for k, c in votes.items()}

    def object_map(self, library) -> dict[str, dict[str, Any]]:
        merged = self.learned_map(library) if library else {}
        for k, v in self.cache.object_map().items():
            merged[k] = {**v, "learned": False}
        return merged

    # ------------------------------------------------------------------ planning

    @staticmethod
    def targets(library) -> list[TargetRef]:
        return [
            TargetRef(r["id"], r["folder"], r["catalogId"], r["name"], r["stage"]["value"], r["sessionRoot"], r["revision"])
            for r in library.revisions.values()
        ]

    def moved_lights(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for row in reversed(self.cache.journal(limit=20000)):
            if row["kind"] == "LIGHT" and not row["undone"] and not row["error"] and row["revision"] not in out[row["downloadNight"]]:
                out[row["downloadNight"]].append(row["revision"])
        return out

    def plan(self) -> Plan | None:
        library = self._library()
        if library is None:
            return None
        return plan_ingest(
            self.cfg.data_root, self.targets(library), self.object_map(library), self.cfg.filter_aliases,
            stable_s=self.stable_s, closed_nights=self.closed_nights(), moved_lights=self.moved_lights(),
        )

    def run_once(self) -> dict[str, Any]:
        """One pass: plan, and in Auto mode apply. Serialized with undo/cleanup."""
        with self._lock:
            mode = self.mode
            if mode == "off":
                self.last_plan = None
                self.last_run = {"at": time.time(), "mode": mode, "moved": 0, "copied": 0, "errors": 0}
                return self.last_run
            plan = self.plan()
            self.last_plan = plan
            moved = copied = errors = 0
            if plan is not None and mode == "auto":
                rows = []
                paused = self.paused_nights()
                for a in plan.actions:
                    if a.download_night in paused:
                        continue
                    row = {"ts": time.time(), "download_night": a.download_night, "op": a.op, "src": a.src, "dst": a.dst,
                           "size": a.size, "revision": a.revision_id, "kind": a.kind, "filter": a.filter, "error": None}
                    try:
                        (fileops.move if a.op == "move" else fileops.copy)(self.cfg.data_root, a.src, a.dst)
                        moved += a.op == "move"
                        copied += a.op == "copy"
                    except Exception as exc:  # noqa: BLE001 — journaled, and the file stays where it was
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        errors += 1
                        log.warning("ingest %s failed: %s → %s: %s", a.op, a.src, a.dst, exc)
                    rows.append(row)
                self.cache.journal_add(rows)
                if moved or copied:
                    self._on_moved()
                    plan = self.plan()
                    self.last_plan = plan
            self.last_run = {"at": time.time(), "mode": mode, "moved": moved, "copied": copied, "errors": errors}
            return self.last_run

    # ------------------------------------------------------------------ undo / cleanup

    def undo_night(self, download_night: str) -> dict[str, Any]:
        with self._lock:
            rows = [r for r in self.cache.journal(download_night) if not r["undone"] and not r["error"]]
            undone, kept_copies, failed = [], [], []
            for r in rows:  # newest first: the inverse order
                if r["op"] == "copy":
                    kept_copies.append(r["dst"])  # removing a copy is a deletion: left for you
                    continue
                try:
                    fileops.move_back(self.cfg.data_root, r["dst"], r["src"])
                    undone.append(r["id"])
                except Exception as exc:  # noqa: BLE001
                    failed.append({"dst": r["dst"], "error": f"{type(exc).__name__}: {exc}"})
            self.cache.journal_mark_undone(undone)
            st = self.cache.get_json("ingest") or {}
            st["closed"] = [n for n in st.get("closed", []) if n != download_night]
            st["paused"] = sorted(set(st.get("paused", [])) | {download_night})
            self.cache.set_json("ingest", st)
            if undone:
                self._on_moved()
            return {"movedBack": len(undone), "copiesLeft": kept_copies, "failed": failed}

    def empty_dirs(self, download_night: str) -> list[str]:
        top = self.cfg.data_root / DOWNLOAD / download_night
        out = []
        if top.is_dir():
            import os

            for dirpath, dirnames, filenames in os.walk(top, topdown=False):
                if not filenames and all(str(Path(dirpath) / d) in out for d in dirnames):
                    out.append(str(Path(dirpath)))
        return out

    def cleanup(self, download_night: str) -> list[str]:
        with self._lock:
            if not NIGHT_DIR_RE.match(download_night):
                raise ValueError("not a night folder name")
            return fileops.rmdir_empty(self.cfg.data_root, str(self.cfg.data_root / DOWNLOAD / download_night))

    # ------------------------------------------------------------------ thread

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ingest", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._library() is not None:
                    self.run_once()
                    self.last_error = None
            except Exception as exc:  # noqa: BLE001
                log.exception("ingest pass failed")
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._wake.wait(self.interval_s)
            self._wake.clear()

    def status(self) -> dict[str, Any]:
        plan = self.last_plan
        return {
            "mode": self.mode,
            "lastRun": self.last_run,
            "error": self.last_error,
            "downloadRoot": str(self.cfg.data_root / DOWNLOAD),
            "downloadExists": (self.cfg.data_root / DOWNLOAD).is_dir(),
            "plan": plan.as_dict() if plan else None,
            "closed": sorted(self.closed_nights()),
            "paused": sorted(self.paused_nights()),
        }
