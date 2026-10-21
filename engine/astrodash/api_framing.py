"""Framing: is each night framed like the night before it? (framing.py measures, this serves.)

Star lists are measured in the background (a process pool, one job at a time, Live first) and
kept in the dashboard database by path and size, so a frame is read once. The Library measures a
sample of every night (SAMPLE lights per night and mosaic panel, spread over the night), a
Diagnostics page every light of its night, Live the newest light as it lands. A night's framing
uses every light of it whose stars are known, so it only gets better as more are measured.

The previous night is the one before in the same revision and panel; a night whose stars all
failed (clouds) is skipped over, so the next one is compared with the last night that has a
framing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from fastapi import FastAPI, HTTPException

from . import framing
from .framing import Night, Stars

log = logging.getLogger(__name__)

SAMPLE = 12             # lights measured per night (and panel) for the Library
NIGHT_CACHE = 400       # night framings kept in memory
KEEP_JOBS = 50          # finished jobs kept for the record

Frame = tuple[str, int]  # (absolute path, size)
Progress = Callable[[int, int], None]


@dataclass
class _Night:
    """One night (one mosaic panel) of a revision: its lights in time order."""

    folder: str
    date: str | None
    frames: list[Frame]


def _sample(frames: list[Frame], n: int = SAMPLE) -> list[Frame]:
    if len(frames) <= n:
        return list(frames)
    step = len(frames) / n
    return [frames[int(i * step + step / 2)] for i in range(n)]


class FramingService:
    def __init__(self, cfg, cache, library: Callable[[], Any]):
        self.cfg = cfg
        self.cache = cache
        self._library = library
        self._root = str(cfg.data_root).replace("\\", "/").rstrip("/") + "/"
        self._lock = threading.RLock()
        self._stars: dict[str, tuple[int, Stars | None]] = {}     # path → (size measured at, stars or None if unreadable)
        self._nights: OrderedDict[tuple, Night | None] = OrderedDict()
        self._compared: OrderedDict[tuple, dict[str, Any] | None] = OrderedDict()
        self._index: tuple[Any, dict] | None = None              # (library, frames per night and panel)
        self._cv = threading.Condition()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._seq = 0
        threading.Thread(target=self._loop, name="framing", daemon=True).start()

    # ------------------------------------------------------------------ stars

    def _load(self, frames: Iterable[Frame]) -> None:
        """Bring known star lists from the database into memory."""
        with self._lock:
            want = list({p for p, _ in frames if p not in self._stars})
        if not want:
            return
        rows = self.cache.frame_stars(want)
        with self._lock:
            for p, (size, data, _) in rows.items():
                self._stars[p] = (size, framing.stars_from_dict(data) if data else None)

    def _known(self, path: str, size: int) -> bool:
        got = self._stars.get(path)
        return got is not None and got[0] == size

    def _get(self, path: str) -> Stars | None:
        got = self._stars.get(path)
        return got[1] if got else None

    def _measure(self, frames: list[Frame], progress: Progress) -> None:
        """Measure the frames' stars (in a process pool beyond a couple) and keep them."""
        todo = [(p, s) for p, s in dict(frames).items() if not self._known(p, s)]
        sizes = dict(todo)
        rows: list[tuple[str, int, dict | None, str | None]] = []
        done = 0
        progress(0, len(todo))

        def record(path: str, data: dict | None, error: str | None) -> None:
            nonlocal done
            rows.append((path, sizes[path], data, error))
            with self._lock:
                self._stars[path] = (sizes[path], framing.stars_from_dict(data) if data else None)
            if error:
                log.info("framing: %s: %s", path, error)
            done += 1
            progress(done, len(todo))
            if len(rows) >= 25:
                self.cache.save_frame_stars(rows)
                rows.clear()

        if len(todo) <= 2:
            for p, _ in todo:
                record(*framing.measure(p))
        elif todo:
            workers = min(len(todo), self.cfg.diagnostics_workers or max(1, min(8, (os.cpu_count() or 2) - 1)))
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for fut in as_completed([pool.submit(framing.measure, p) for p, _ in todo]):
                    record(*fut.result())
        self.cache.save_frame_stars(rows)

    # ------------------------------------------------------------------ jobs

    def _submit(self, key: str, label: str, frames: set[Frame], priority: int) -> dict[str, Any] | None:
        """Queue measuring `frames` (0: Live, 1: a page, 2: background); a job of the same key is reused."""
        if not frames:
            return None
        with self._cv:
            job = self._jobs.get(key)
            if job and job["status"] == "running":
                return self._view(job)
            if job and job["status"] == "queued":
                job["frames"] |= frames
                job["total"] = len(job["frames"])
                job["priority"] = min(job["priority"], priority)
                return self._view(job)
            self._seq += 1
            job = {"key": key, "label": label, "status": "queued", "priority": priority, "seq": self._seq,
                   "frames": set(frames), "done": 0, "total": len(frames), "error": None, "finished": None}
            self._jobs[key] = job
            self._cv.notify()
            return self._view(job)

    @staticmethod
    def _view(job: dict[str, Any]) -> dict[str, Any]:
        return {k: job[k] for k in ("label", "status", "done", "total", "error")}

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not (queued := [j for j in self._jobs.values() if j["status"] == "queued"]):
                    self._cv.wait()
                job = min(queued, key=lambda j: (j["priority"], j["seq"]))
                job["status"] = "running"
                frames = list(job["frames"])

            def progress(done: int, total: int) -> None:
                job["done"], job["total"] = done, total

            try:
                self._measure(frames, progress)
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001 — a failed job is reported; the next request queues another
                log.exception("framing job %s failed", job["key"])
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
            job["finished"] = time.time()
            with self._cv:
                ended = sorted((j for j in self._jobs.values() if j["finished"]), key=lambda j: j["finished"])
                for j in ended[:-KEEP_JOBS]:
                    del self._jobs[j["key"]]

    def jobs(self) -> list[dict[str, Any]]:
        with self._cv:
            return [self._view(j) for j in sorted(self._jobs.values(), key=lambda j: j["seq"])]

    # ------------------------------------------------------------------ nights

    def _frame_index(self, lib) -> dict[tuple[str, str], dict[str | None, list[Frame]]]:
        """(target folder, night folder) → mosaic panel → the night's kept lights in time order."""
        with self._lock:
            if self._index and self._index[0] is lib:
                return self._index[1]
        groups: dict[tuple[str, str], dict[str | None, list]] = defaultdict(lambda: defaultdict(list))
        for f in lib.ok_lights:
            if f.target_folder and f.session_folder:
                groups[(f.target_folder, f.session_folder)][f.location.panel if f.location else None].append(f)
        index = {
            k: {panel: [(f.path, f.size) for f in sorted(fs, key=lambda f: (f.time_utc is None, f.time_utc.timestamp() if f.time_utc else 0, f.path))]
                for panel, fs in by_panel.items()}
            for k, by_panel in groups.items()
        }
        with self._lock:
            self._index = (lib, index)
        return index

    def _chains(self, lib, rev: dict[str, Any]) -> dict[str | None, list[_Night]]:
        """Per mosaic panel (None: no panels), the revision's nights with lights, oldest first."""
        index = self._frame_index(lib)
        chains: dict[str | None, list[_Night]] = defaultdict(list)
        for s in rev["sessions"]:
            for panel, frames in index.get((rev["folder"], s["folder"]), {}).items():
                chains[panel].append(_Night(s["folder"], s["date"], frames))
        return chains

    def _framing(self, frames: list[Frame]) -> tuple[tuple, Night | None]:
        """The framing of a night from these frames (their stars known), cached by the frames."""
        key = tuple(frames)
        with self._lock:
            if key in self._nights:
                self._nights.move_to_end(key)
                return key, self._nights[key]
        stars = {p: s for p, _ in frames if (s := self._get(p)) is not None}
        result = framing.night(stars, [p for p, _ in frames])
        with self._lock:
            self._nights[key] = result
            while len(self._nights) > NIGHT_CACHE:
                self._nights.popitem(last=False)
        return key, result

    def _compare(self, ref: tuple[_Night, tuple, Night] | None, n: _Night, key: tuple, cur: Night) -> dict[str, Any]:
        base = {"frames": len(cur.poses), "outliers": len(cur.outliers), "failed": len(cur.failed),
                "spreadPx": round(cur.spread, 1)}
        if ref is None:
            return {"status": "first", **base}
        rn, rkey, rnight = ref
        pair = (rkey, key)
        with self._lock:
            known = pair in self._compared
            res = self._compared.get(pair)
        if not known:
            res = framing.compare(rnight, cur, self._get(rnight.anchor), self._get(cur.anchor))
            with self._lock:
                self._compared[pair] = res
                while len(self._compared) > NIGHT_CACHE:
                    self._compared.popitem(last=False)
        info = {"previous": rn.folder, "previousDate": rn.date}
        if res is None:
            return {"status": "unmatched", **base, **info}
        return {"status": "ready", **base, **info, **res}

    def _evaluate(self, chain: list[_Night], full: str | None = None
                  ) -> tuple[list[dict[str, Any]], set[Frame], tuple[_Night, tuple, Night] | None]:
        """Verdicts for one panel's nights (oldest first), the frames still to measure, and the
        last night with a framing (the reference for whatever comes next).

        `full`: the night whose every light is wanted (a Diagnostics page), not just a sample.
        Once a night waits for its stars, every later one waits too (it may be their reference).
        """
        self._load(f for n in chain for f in n.frames)
        verdicts: list[dict[str, Any]] = []
        missing: set[Frame] = set()
        ref: tuple[_Night, tuple, Night] | None = None
        waiting = False
        for n in chain:
            wanted = n.frames if n.folder == full else _sample(n.frames)
            need = [f for f in wanted if not self._known(*f)]
            missing.update(need)
            if need or waiting:
                waiting = True
                verdicts.append({"status": "pending"})
                continue
            key, cur = self._framing([f for f in n.frames if self._known(*f)])
            if cur is None:
                verdicts.append({"status": "unmeasured"})
                continue
            verdicts.append(self._compare(ref, n, key, cur))
            ref = (n, key, cur)
        return verdicts, missing, (None if waiting else ref)

    def _rel(self, path: str) -> str:
        return path.replace("\\", "/").removeprefix(self._root)

    # ------------------------------------------------------------------ views

    def revision(self, rev_id: str) -> dict[str, Any] | None:
        """Every night of a revision (per mosaic panel) against the night before it."""
        lib = self._library()
        rev = lib.revisions.get(rev_id) if lib else None
        if rev is None:
            return None
        sessions: dict[str, dict[str, Any]] = defaultdict(dict)
        missing: set[Frame] = set()
        chains = self._chains(lib, rev)
        for panel, chain in chains.items():
            verdicts, need, _ = self._evaluate(chain)
            missing |= need
            for n, v in zip(chain, verdicts):
                sessions[n.folder][panel or ""] = {k: x for k, x in v.items() if k != "byFrame"}
        job = self._submit(f"revision:{rev_id}", rev["folder"], missing, priority=1)
        return {"revisionId": rev_id, "panels": sorted((p for p in chains if p), key=lambda p: int(p) if p.isdigit() else 0),
                "sessions": sessions, "job": job, "minTolerancePx": framing.MIN_TOLERANCE_PX}

    def session(self, rev_id: str, folder: str) -> dict[str, Any] | None:
        """One night against the previous one, with every light of it (keyed like Diagnostics' frames)."""
        lib = self._library()
        rev = lib.revisions.get(rev_id) if lib else None
        if rev is None or not any(s["folder"] == folder for s in rev["sessions"]):
            return None
        panels, missing = self._session(rev, lib, folder)
        job = self._submit(f"session:{rev_id}/{folder}", f"{rev['folder']} · {folder}", missing, priority=1)
        return {"revisionId": rev_id, "session": folder, "panels": panels, "job": job,
                "minTolerancePx": framing.MIN_TOLERANCE_PX}

    def _session(self, rev: dict[str, Any], lib, folder: str) -> tuple[list[dict[str, Any]], set[Frame]]:
        out, missing = [], set()
        for panel, chain in sorted(self._chains(lib, rev).items(), key=lambda kv: int(kv[0]) if kv[0] and kv[0].isdigit() else 0):
            upto = next((i for i, n in enumerate(chain) if n.folder == folder), None)
            if upto is None:
                continue
            verdicts, need, _ = self._evaluate(chain[: upto + 1], full=folder)
            missing |= need
            v = dict(verdicts[-1])
            frames = {self._rel(p): x for p, x in (v.pop("byFrame", None) or {}).items()}
            out.append({"panel": panel, "verdict": v, "frames": frames})
        return out, missing

    def prepare_session(self, rev_id: str, folder: str, progress: Callable[[str, int, int], None]) -> None:
        """Measure a night's every light (and earlier nights' samples) now: Diagnostics' last phase."""
        lib = self._library()
        rev = lib.revisions.get(rev_id) if lib else None
        if rev is None:
            return
        _, missing = self._session(rev, lib, folder)
        self._measure(sorted(missing), lambda done, total: progress("framing", done, total))

    def live(self, path: str, revision_id: str | None, panel: str | None, night: str | None) -> dict[str, Any]:
        """The newest light against the last night before `night` of its revision (and panel)."""
        try:
            size = os.stat(path).st_size
        except OSError:
            return {"status": "unmeasured", "frame": Path(path).name}
        lib = self._library()
        rev = lib.revisions.get(revision_id) if lib and revision_id else None
        base = {"frame": Path(path).name, "revisionId": rev["id"] if rev else None,
                "target": rev["folder"] if rev else None, "panel": panel}
        if rev is None:
            return {"status": "no-target", **base}
        chain = [n for n in self._chains(lib, rev).get(panel, []) if n.date and night and n.date < night]
        self._load([(path, size)])
        _, missing, ref = self._evaluate(chain)
        if not self._known(path, size):
            missing.add((path, size))
        if missing:
            return {"status": "pending", **base, "job": self._submit(f"live:{path}", Path(path).name, missing, priority=0)}
        if ref is None:
            return {"status": "first", **base}
        rn, rkey, rnight = ref
        info = {**base, "previous": rn.folder, "previousDate": rn.date}
        frame = self._get(path)
        pair = (rkey, ((path, size),))
        with self._lock:
            known = pair in self._compared
            res = self._compared.get(pair)
        if not known:
            res = framing.compare_frame(rnight, self._get(rnight.anchor), frame) if frame is not None else None
            with self._lock:
                self._compared[pair] = res
        if frame is None:
            return {"status": "unmeasured", **info}
        if res is None:
            return {"status": "unmatched", **info}
        return {"status": "ready", **info, **res}

    def warm(self) -> None:
        """After a scan: queue every night's sample not measured yet, one background job per revision."""
        lib = self._library()
        if lib is None:
            return
        for rev in lib.revisions.values():
            frames = [f for chain in self._chains(lib, rev).values() for n in chain for f in _sample(n.frames)]
            self._load(frames)
            need = {f for f in frames if not self._known(*f)}
            if need:
                self._submit(f"revision:{rev['id']}", rev["folder"], need, priority=2)


def register(app: FastAPI, state) -> None:
    svc: FramingService = state.framing

    @app.get("/api/framing/revisions/{rev_id}")
    def revision_framing(rev_id: str) -> dict[str, Any]:
        state.require_library()
        out = svc.revision(rev_id)
        if out is None:
            raise HTTPException(404, detail="No such target revision")
        return out

    @app.get("/api/framing/sessions/{rev_id}/{session}")
    def session_framing(rev_id: str, session: str) -> dict[str, Any]:
        state.require_library()
        out = svc.session(rev_id, session)
        if out is None:
            raise HTTPException(404, detail="No such session")
        return out

    @app.get("/api/framing/jobs")
    def framing_jobs() -> dict[str, Any]:
        return {"jobs": svc.jobs()}
