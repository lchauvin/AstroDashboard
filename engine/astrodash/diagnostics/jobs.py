"""A one-at-a-time queue of diagnostics runs (each run already uses every core)."""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..cache import HeaderCache
from ..config import Config
from . import overrides
from .runner import run_session

log = logging.getLogger(__name__)


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """Small per-session summary for listings (the full result stays in its own row)."""
    return {
        "lights": [
            {"key": g["key"], "filter": g["filter"], **{k: g["summary"][k] for k in ("frames", "kept", "rejected", "keptSeconds")},
             "fwhm": g["summary"]["median"]["fwhm_median"]}
            for g in result["lights"]
        ],
        "flats": [
            {"key": f["key"], "filter": f["filter"], "verdict": (f.get("summary") or {}).get("verdict")}
            for f in result["flats"]
        ],
        "calibration": [
            {"light": r["light"], "status": r["status"]} for r in (result.get("calibration") or {}).get("rows", [])
        ],
        "errors": len(result.get("errors", [])),
    }


class DiagnosticsJobs:
    def __init__(self, cfg: Config, cache: HeaderCache, resolve: Callable[[str, str], dict[str, Any] | None],
                 runner: Callable[..., dict[str, Any]] = run_session, after: Callable[[], None] | None = None):
        self.cfg = cfg
        self.cache = cache
        self.resolve = resolve          # (revision id, session folder) -> library session dict
        self.runner = runner
        self.after = after              # run once a result is saved, before the job reads as done
        # (revision, session, progress) → measure the night's framing; set by the engine, not in tests.
        self.framing: Callable[[str, str, Callable[[str, int, int], None]], None] | None = None
        self._ids = itertools.count(1)
        self._queue: deque[dict[str, Any]] = deque()
        self._jobs: dict[int, dict[str, Any]] = {}
        self._cv = threading.Condition()
        threading.Thread(target=self._loop, name="diagnostics", daemon=True).start()

    def submit(self, revision: str, session: str) -> dict[str, Any]:
        with self._cv:
            for job in self._jobs.values():
                if job["revisionId"] == revision and job["session"] == session and job["status"] in ("queued", "running"):
                    return job
            job = {
                "id": next(self._ids), "revisionId": revision, "session": session, "status": "queued",
                "phase": "queued", "done": 0, "total": 0, "submitted": time.time(),
                "started": None, "finished": None, "error": None,
            }
            self._jobs[job["id"]] = job
            self._queue.append(job)
            self._cv.notify()
            return job

    def list(self) -> list[dict[str, Any]]:
        with self._cv:
            return [dict(j) for j in sorted(self._jobs.values(), key=lambda j: j["id"])]

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                job = self._queue.popleft()
                job["status"] = "running"
                job["started"] = time.time()
            try:
                self._run(job)
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                log.exception("diagnostics failed for %s / %s", job["revisionId"], job["session"])
                job["status"] = "failed"
                job["error"] = f"{type(exc).__name__}: {exc}"
            job["finished"] = time.time()

    def _run(self, job: dict[str, Any]) -> None:
        session = self.resolve(job["revisionId"], job["session"])
        if session is None or not session.get("path"):
            raise FileNotFoundError(f"No session folder for {job['revisionId']} / {job['session']}")

        def progress(phase: str, done: int, total: int) -> None:
            job["phase"], job["done"], job["total"] = phase, done, total

        t0 = time.time()
        site = tuple(session["site"]) if session.get("site") else None
        result = self.runner(
            Path(session["path"]), self.cfg.data_root, self.cfg.astro_eval_path,
            site=site, workers=self.cfg.diagnostics_workers or None, progress=progress,
        )
        previous = self.cache.diagnostic(job["revisionId"], job["session"])
        overrides.carry_over(previous["result"] if previous else None, result)
        self.cache.save_diagnostic(job["revisionId"], job["session"], time.time() - t0,
                                   summarize(overrides.apply(result)), result)
        if self.after:
            # astro-eval read the folder as it is now; bring the Library up to date with it too
            # (frames removed or moved since the last scan), so target pages agree with the diagnostic.
            progress("updating library", 0, 0)
            self.after()
        if self.framing:
            try:
                self.framing(job["revisionId"], job["session"], progress)
            except Exception:  # noqa: BLE001 — the analysis is saved; the framing is measured again on demand
                log.exception("framing failed for %s / %s", job["revisionId"], job["session"])
