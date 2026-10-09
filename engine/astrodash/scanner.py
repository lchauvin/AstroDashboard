"""Walks the data root and reads headers for new or changed frames.

Read-only by construction: this module only lists directories, stats files and
opens them in "rb" mode through `headers.read_header`.

Only raw-data areas are walked file by file (`.SessionData`, the mispointed
quarantine, the global `.Calibration` library). Processing folders (WBPP,
1.–7., .Finished) are only inspected shallowly for the status badge.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cache import HeaderCache
from .headers import HEADER_VERSION, IMAGE_SUFFIXES, read_header
from .layout import (
    QUARANTINE_PREFIX,
    SESSION_DATA,
    YEAR_RE,
    TargetName,
    parse_target_folder,
)

log = logging.getLogger(__name__)

LEGACY_STAGE_RE = re.compile(r"^[1-7]\.\s")
FINISHED_PREVIEW_SUFFIXES = (".jpg", ".jpeg", ".png")


@dataclass
class FrameFile:
    path: str                  # absolute path
    area: str                  # "session" | "quarantine" | "library"
    target_folder: str | None  # None for the library
    rel_parts: list[str]       # components below .SessionData / quarantine dir / .Calibration
    size: int
    mtime: float
    header: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class TargetFolderInfo:
    target: TargetName
    path: str
    legacy_years: list[str] = field(default_factory=list)
    legacy_stages: list[str] = field(default_factory=list)     # non-empty 1.–7. folders
    wbpp_masters: list[str] = field(default_factory=list)      # WBPP*/master folders with files
    finished_files: int = 0
    finished_preview: str | None = None                        # newest jpg/png in .Finished
    master_flats: list[str] = field(default_factory=list)      # MasterFlat_* paths
    target_json: dict[str, Any] | None = None


@dataclass
class ScanResult:
    frames: list[FrameFile]
    targets: list[TargetFolderInfo]
    finished_at: float
    duration_s: float
    files_read: int
    read_errors: int


@dataclass
class ScanProgress:
    running: bool = False
    phase: str = "idle"
    files_found: int = 0
    to_read: int = 0
    read: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _is_image(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_SUFFIXES


def _walk_files(root: str):
    """Yield (path, rel_parts, stat) for every image file below root."""
    stack: list[tuple[str, list[str]]] = [(root, [])]
    while stack:
        current, rel = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, rel + [entry.name]))
                    elif entry.is_file(follow_symlinks=False) and _is_image(entry.name):
                        try:
                            st = entry.stat()
                        except OSError:
                            continue
                        yield entry.path, rel + [entry.name], st
        except OSError as exc:
            log.warning("cannot list %s: %s", current, exc)


def _has_files(path: str, max_depth: int = 3) -> bool:
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False):
                    return True
                if max_depth > 0 and entry.is_dir(follow_symlinks=False) and _has_files(entry.path, max_depth - 1):
                    return True
    except OSError:
        return False
    return False


def _list_dirs(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _inspect_root(root: str, info: TargetFolderInfo, frames: list[FrameFile]) -> None:
    """One revision root: the target folder itself or a legacy <YEAR> folder below it."""
    for entry in _list_dirs(root):
        name = entry.name
        if name == SESSION_DATA:
            for path, rel, st in _walk_files(entry.path):
                frames.append(FrameFile(path, "session", info.target.folder, rel, st.st_size, st.st_mtime))
        elif name.startswith(QUARANTINE_PREFIX):
            for path, rel, st in _walk_files(entry.path):
                frames.append(FrameFile(path, "quarantine", info.target.folder, rel, st.st_size, st.st_mtime))
        elif name == ".Calibration":
            for path, rel, _ in _walk_files(entry.path):
                if rel[-1].casefold().startswith("masterflat_"):
                    info.master_flats.append(path)
        elif name == ".Finished":
            _inspect_finished(entry.path, info)
        elif LEGACY_STAGE_RE.match(name):
            if _has_files(entry.path):
                info.legacy_stages.append(name)
        elif name.upper().startswith("WBPP"):
            master = os.path.join(entry.path, "master")
            if os.path.isdir(master) and _has_files(master, 1):
                info.wbpp_masters.append(os.path.relpath(master, info.path))


def _inspect_finished(path: str, info: TargetFolderInfo) -> None:
    newest: tuple[float, str] | None = None
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                info.finished_files += 1
                if entry.name.lower().endswith(FINISHED_PREVIEW_SUFFIXES):
                    mtime = entry.stat().st_mtime
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, entry.path)
    except OSError:
        return
    if newest and (info.finished_preview is None or newest[1] > info.finished_preview):
        info.finished_preview = newest[1]


def _read_target_json(path: str) -> dict[str, Any] | None:
    candidate = os.path.join(path, "target.json")
    if not os.path.isfile(candidate):
        return None
    try:
        with open(candidate, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("unreadable %s: %s", candidate, exc)
        return None


def discover(data_root: Path) -> tuple[list[FrameFile], list[TargetFolderInfo]]:
    frames: list[FrameFile] = []
    targets: list[TargetFolderInfo] = []
    root = str(data_root)
    for entry in sorted(_list_dirs(root), key=lambda e: e.name.casefold()):
        name = entry.name
        if name == ".Calibration":
            for path, rel, st in _walk_files(entry.path):
                frames.append(FrameFile(path, "library", None, rel, st.st_size, st.st_mtime))
            continue
        if name.startswith("."):
            continue
        info = TargetFolderInfo(parse_target_folder(name), entry.path)
        info.target_json = _read_target_json(entry.path)
        _inspect_root(entry.path, info, frames)
        for sub in _list_dirs(entry.path):
            if YEAR_RE.match(sub.name):
                info.legacy_years.append(sub.name)
                _inspect_root(sub.path, info, frames)
        info.legacy_years.sort()
        targets.append(info)
    return frames, targets


def _read_one(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return read_header(Path(path)), None
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop the scan
        return None, f"{type(exc).__name__}: {exc}"


class Scanner:
    def __init__(self, data_root: Path, cache: HeaderCache, workers: int = 16):
        self.data_root = data_root
        self.cache = cache
        self.workers = workers
        self.progress = ScanProgress()
        self._lock = threading.Lock()

    def scan(self) -> ScanResult:
        with self._lock:
            started = time.time()
            self.progress = ScanProgress(running=True, phase="listing", started_at=started)
            try:
                return self._scan(started)
            except Exception as exc:
                self.progress.error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                self.progress.running = False
                self.progress.finished_at = time.time()
                if self.progress.error is None:
                    self.progress.phase = "done"

    def _scan(self, started: float) -> ScanResult:
        if not self.data_root.is_dir():
            raise FileNotFoundError(f"data root not found: {self.data_root}")
        frames, targets = discover(self.data_root)
        self.progress.files_found = len(frames)

        cached = self.cache.load_all()
        if self.cache.get_meta("header_version") != HEADER_VERSION:
            cached = {}   # extracted keys changed: re-read every header once
        todo: list[FrameFile] = []
        for frame in frames:
            hit = cached.get(frame.path)
            if hit and hit[0] == frame.size and abs(hit[1] - frame.mtime) < 1e-3:
                frame.header, frame.error = hit[2], hit[3]
            else:
                todo.append(frame)

        self.progress.phase = "reading headers"
        self.progress.to_read = len(todo)
        batch: list[tuple] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for frame, (header, error) in zip(todo, pool.map(lambda f: _read_one(f.path), todo)):
                frame.header, frame.error = header, error
                batch.append((frame.path, frame.size, frame.mtime, header, error))
                self.progress.read += 1
                if len(batch) >= 500:
                    self.cache.upsert(batch)
                    batch.clear()
        self.cache.upsert(batch)

        seen = {f.path for f in frames}
        self.cache.forget(p for p in cached if p not in seen)
        finished = time.time()
        self.cache.set_meta("last_scan", str(finished))
        self.cache.set_meta("header_version", HEADER_VERSION)
        return ScanResult(
            frames=frames,
            targets=targets,
            finished_at=finished,
            duration_s=finished - started,
            files_read=len(todo),
            read_errors=sum(1 for f in todo if f.error),
        )
