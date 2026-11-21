"""Match: per target and filter, the quality gathered per hour (match.py measures, this serves).

Only analysed nights count (Diagnostics decides which lights are kept), and a night left out
of the target does not. Binned frames and flats are kept in the dashboard database, so a frame
is read once; the per-target result is recomputed from them (cheap) whenever its frames change.
Measuring runs in the background, one target at a time, a page's request before the warm-up.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from statistics import median
from typing import Any, Callable

from fastapi import FastAPI, HTTPException

from . import match
from .diagnostics import overrides
from .frames import canonical_filter
from .planning import bias_pedestal

log = logging.getLogger(__name__)

SAMPLE = 6          # kept lights per night and filter
FLATS = 8           # flats per night and filter
KEEP_JOBS = 50


def _spread(items: list, n: int) -> list:
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step + step / 2)] for i in range(n)]


class MatchService:
    def __init__(self, cfg, cache, library: Callable[[], Any]):
        self.cfg = cfg
        self.cache = cache
        self._library = library
        self._lock = threading.Lock()
        self._results: dict[str, tuple[tuple, dict[str, Any]]] = {}   # revision → (signature, result)
        self._panels: tuple[Any, dict[str, str | None]] | None = None  # (library, rel path → panel)
        self._nights: dict[tuple[str, str], tuple[tuple, dict[str, Any]]] = {}  # (revision, night) → (stamp, info)
        self._cv = threading.Condition()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._seq = 0
        threading.Thread(target=self._loop, name="match", daemon=True).start()

    # ------------------------------------------------------------------ inputs

    def _abs(self, rel: str) -> str:
        """A diagnostics path ("Target/…") on whichever disk holds the target now."""
        return str(self.cfg.locate(rel)).replace("\\", "/")

    def _panel_of(self, lib) -> dict[str, str | None]:
        with self._lock:
            if self._panels and self._panels[0] is lib:
                return self._panels[1]
        out = {self.cfg.rel_path(f.path): (f.location.panel if f.location else None) for f in lib.ok_lights}
        with self._lock:
            self._panels = (lib, out)
        return out

    def _night(self, rev_id: str, folder: str, stamp: tuple) -> dict[str, Any] | None:
        """A night's sampled kept lights and its flats per filter, from its diagnostic (cached by its stamp)."""
        key = (rev_id, folder)
        with self._lock:
            got = self._nights.get(key)
        if got and got[0] == stamp:
            return got[1]
        stored = self.cache.diagnostic(rev_id, folder)
        if not stored:
            return None
        result = overrides.apply(stored["result"])
        flats: dict[str, list[str]] = defaultdict(list)
        for fs in result.get("flats", []):
            for fr in fs.get("frames", []):
                filt = canonical_filter(fr.get("filter_name"), self.cfg.filter_aliases)
                if not fr.get("rejected") and fr.get("path") and filt:
                    flats[filt].append(self._abs(fr["path"]))
        lights = []
        for group in result.get("lights", []):
            kept = [fr for fr in group.get("frames", []) if not fr.get("rejected") and fr.get("path")]
            for fr in _spread(kept, SAMPLE):
                filt = canonical_filter(fr.get("filter_name"), self.cfg.filter_aliases)
                if filt and fr.get("noise_mad") and fr.get("exptime"):
                    lights.append({"rel": fr["path"], "filter": filt,
                                   "noise": float(fr["noise_mad"]), "exptime": float(fr["exptime"])})
        info = {"lights": lights, "flats": {k: sorted(v)[:FLATS] for k, v in flats.items()}}
        with self._lock:
            self._nights[key] = (stamp, info)
        return info

    def _stamps(self) -> dict[tuple[str, str], tuple]:
        return {(r["revisionId"], r["session"]): (r["finished"], repr(r["summary"])) for r in self.cache.diagnostic_summaries()}

    def _plan(self, lib, rev: dict[str, Any], stamps: dict[tuple[str, str], tuple] | None = None) -> dict[str, Any]:
        """What the revision's result needs: sampled kept lights and the night's flats per filter."""
        stamps = self._stamps() if stamps is None else stamps
        panels = self._panel_of(lib)
        lights: list[dict[str, Any]] = []
        flats: dict[str, list[str]] = {}
        analysed = unanalysed = 0
        pedestals: dict[tuple, float | None] = {}
        for s in rev["sessions"]:
            if s.get("ignored"):
                continue
            stamp = stamps.get((rev["id"], s["folder"]))
            night = self._night(rev["id"], s["folder"], stamp) if stamp else None
            if night is None:
                unanalysed += 1
                continue
            analysed += 1
            for fr in night["lights"]:
                rel, filt = fr["rel"], fr["filter"]
                header = lib.light_headers.get(rel)
                if not header:
                    continue
                gi = self.cfg.gain_ignored(header.get("READOUTM"))
                pkey = (header.get("INSTRUME"), header.get("READOUTM"),
                        None if gi else header.get("GAIN"), None if gi else header.get("OFFSET"))
                if pkey not in pedestals:
                    ped = bias_pedestal(self.cache, lib.library_frames, *pkey, self.cfg.min_bias_exposure_s)
                    pedestals[pkey] = ped["adu"] if ped else None
                fpaths = night["flats"].get(filt, [])
                fkey = match.flat_key(fpaths, 0.0) if fpaths else None
                if fkey:
                    flats[fkey] = fpaths
                scale = None
                if header.get("XPIXSZ") and header.get("FOCALLEN"):
                    scale = 206.265 * float(header["XPIXSZ"]) / float(header["FOCALLEN"])
                lights.append({
                    "path": self._abs(rel), "filter": filt, "night": s["folder"], "panel": panels.get(rel),
                    "noise": fr["noise"], "exptime": fr["exptime"], "pedestal": pedestals[pkey],
                    "flipped": str(header.get("PIERSIDE", "")).strip().lower() == "west", "flat": fkey, "scale": scale,
                })
        return {"lights": lights, "flats": flats, "analysed": analysed, "unanalysed": unanalysed}

    @staticmethod
    def _size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return -1

    def _missing(self, plan: dict[str, Any], known: dict[str, tuple[int, bytes | None, str | None]]) -> tuple[list[str], list[str]]:
        lights = [f["path"] for f in plan["lights"] if f["path"] not in known or known[f["path"]][0] != self._size(f["path"])]
        flats = [k for k in plan["flats"] if k not in known]
        return lights, flats

    # ------------------------------------------------------------------ measuring

    def _measure(self, lights: list[str], flats: dict[str, list[str]], progress: Callable[[int, int], None]) -> None:
        total = len(lights) + len(flats)
        done = 0
        rows: list[tuple[str, int, bytes | None, str | None]] = []
        progress(0, total)

        def record(key: str, size: int, data: bytes | None, error: str | None) -> None:
            nonlocal done
            rows.append((key, size, data, error))
            if error:
                log.info("match: %s: %s", key, error)
            done += 1
            progress(done, total)
            if len(rows) >= 25:
                self.cache.save_match_frames(rows)
                rows.clear()

        if total <= 2:
            for p in lights:
                record(p, self._size(p), *match.measure_frame(p)[1:])
            for k, ps in flats.items():
                record(k, 0, *match.measure_flat(k, ps, 0.0)[1:])
        elif total:
            workers = min(total, self.cfg.diagnostics_workers or max(1, min(8, (os.cpu_count() or 2) - 1)))
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(match.measure_frame, p): ("light", p) for p in lights}
                futs |= {pool.submit(match.measure_flat, k, ps, 0.0): ("flat", k) for k, ps in flats.items()}
                for fut in as_completed(futs):
                    kind, key = futs[fut]
                    _, data, error = fut.result()
                    record(key, self._size(key) if kind == "light" else 0, data, error)
        self.cache.save_match_frames(rows)

    def _submit(self, rev_id: str, label: str, priority: int) -> dict[str, Any]:
        with self._cv:
            job = self._jobs.get(rev_id)
            if job and job["status"] in ("queued", "running"):
                job["priority"] = min(job["priority"], priority)
                return self._view(job)
            self._seq += 1
            job = {"key": rev_id, "label": label, "status": "queued", "priority": priority, "seq": self._seq,
                   "done": 0, "total": 0, "error": None, "finished": None}
            self._jobs[rev_id] = job
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

            def progress(done: int, total: int) -> None:
                job["done"], job["total"] = done, total

            try:
                lib = self._library()
                rev = lib.revisions.get(job["key"]) if lib else None
                if rev is not None:
                    plan = self._plan(lib, rev)
                    known = self.cache.match_frames([f["path"] for f in plan["lights"]] + list(plan["flats"]))
                    lights, flat_keys = self._missing(plan, known)
                    self._measure(lights, {k: plan["flats"][k] for k in flat_keys}, progress)
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001 — reported; the next request queues another
                log.exception("match job %s failed", job["key"])
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
            job["finished"] = time.time()
            with self._cv:
                ended = sorted((j for j in self._jobs.values() if j["finished"]), key=lambda j: j["finished"])
                for j in ended[:-KEEP_JOBS]:
                    del self._jobs[j["key"]]

    def jobs(self) -> list[dict[str, Any]]:
        with self._cv:
            return [self._view(j) for j in sorted(self._jobs.values(), key=lambda j: j["seq"])]

    # ------------------------------------------------------------------ results

    def _compute(self, plan: dict[str, Any], known: dict[str, tuple[int, bytes | None, str | None]]) -> dict[str, Any]:
        frames: list[dict[str, Any]] = []
        no_flat = no_pedestal = unreadable = 0
        for f in plan["lights"]:
            raw = known.get(f["path"])
            if not raw or raw[1] is None:
                unreadable += 1
                continue
            flat = known.get(f["flat"]) if f["flat"] else None
            if not flat or flat[1] is None:
                no_flat += 1
                continue
            if f["pedestal"] is None:
                no_pedestal += 1
                continue
            img_raw, img_flat = match.from_bytes(raw[1]), match.from_bytes(flat[1])
            if img_raw.shape != img_flat.shape:
                no_flat += 1
                continue
            frames.append({**f, "img": match.calibrated(img_raw, img_flat, f["pedestal"], f["flipped"])})
        filters: dict[str, dict[str, Any]] = {}
        masks: dict[str, str] = {}
        by_panel: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for f in frames:
            by_panel[f["panel"]].append(f)
        per_filter: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
        for panel, fs in by_panel.items():
            deepest = match.mask_filter(fs)
            masks[panel or ""] = deepest
            for filt, summary in match.summarize(fs, deepest).items():
                per_filter[filt].append((summary, summary["frames"]))
        for filt, parts in per_filter.items():
            n = sum(w for _, w in parts)
            filters[filt] = {
                "qPerHour": round(sum(s["qPerHour"] * w for s, w in parts) / n, 5),
                "spread": parts[0][0]["spread"] if len(parts) == 1 else None,
                "frames": n,
                "nights": len({f["night"] for f in frames if f["filter"] == filt}),
            }
        scales = [f["scale"] for f in frames if f["scale"]]
        return {"filters": filters, "maskFilter": masks, "scale": round(median(scales), 3) if scales else None,
                "skipped": {"noFlat": no_flat, "noPedestal": no_pedestal, "unreadable": unreadable}}

    def revision(self, rev_id: str, priority: int = 1, stamps: dict | None = None) -> dict[str, Any] | None:
        lib = self._library()
        rev = lib.revisions.get(rev_id) if lib else None
        if rev is None:
            return None
        plan = self._plan(lib, rev, stamps)
        keys = [f["path"] for f in plan["lights"]] + list(plan["flats"])
        known = self.cache.match_frames(keys)
        lights, flats = self._missing(plan, known)
        job = self._submit(rev_id, rev["folder"], priority) if (lights or flats) else None
        sig = tuple((k, known[k][0]) for k in sorted(known))
        with self._lock:
            cached = self._results.get(rev_id)
        if cached and cached[0] == sig:
            result = cached[1]
        else:
            result = self._compute(plan, known)
            with self._lock:
                self._results[rev_id] = (sig, result)
        return {"revisionId": rev_id, **result, "analysedNights": plan["analysed"], "unanalysedNights": plan["unanalysed"],
                "pending": bool(lights or flats), "job": job}

    def summary(self) -> dict[str, Any]:
        """Every revision's result as far as measured (missing frames are queued in the background)."""
        lib = self._library()
        if lib is None:
            return {}
        stamps = self._stamps()
        return {rev_id: self.revision(rev_id, priority=2, stamps=stamps) for rev_id in lib.revisions}

    def queue(self, rev_id: str) -> None:
        """After an analysis: measure the revision's new frames in the background."""
        lib = self._library()
        rev = lib.revisions.get(rev_id) if lib else None
        if rev is not None:
            self._submit(rev_id, rev["folder"], priority=1)

    def warm(self) -> None:
        """After a scan: queue every revision with frames still to measure."""
        lib = self._library()
        if lib is None:
            return
        stamps = self._stamps()
        for rev_id, rev in lib.revisions.items():
            plan = self._plan(lib, rev, stamps)
            known = self.cache.match_frames([f["path"] for f in plan["lights"]] + list(plan["flats"]))
            if any(self._missing(plan, known)):
                self._submit(rev_id, rev["folder"], priority=2)


def register(app: FastAPI, state) -> None:
    svc: MatchService = state.match

    @app.get("/api/match/revisions/{rev_id}")
    def revision_match(rev_id: str) -> dict[str, Any]:
        state.require_library()
        out = svc.revision(rev_id)
        if out is None:
            raise HTTPException(404, detail="No such target revision")
        return out

    @app.get("/api/match/summary")
    def match_summary() -> dict[str, Any]:
        state.require_library()
        return {"revisions": svc.summary()}

    @app.get("/api/match/jobs")
    def match_jobs() -> dict[str, Any]:
        return {"jobs": svc.jobs()}
