"""The only code in the engine that changes anything under the data root (PLAN §2.1, §5.1).

Every operation re-checks its paths, whatever the caller believes:

The download folder (`<data root>\\.Download` unless Settings say otherwise) is passed in and checked
first (`paths.problem`): on the data root's drive, never holding the data root, and inside the data
root only a dot folder directly under it.

- `move`: a same-volume rename from the download folder into a revision's
  `.SessionData\\NIGHT_<date>\\…`. Never overwrites (the rename itself refuses
  an existing destination on Windows, and it is checked first anyway).
- `copy`: the same destinations, for flats shared by several targets. Written
  to a `.part` file opened with exclusive create, then renamed into place; the
  `.part` file is the only thing this module ever removes, and only when it
  created it and the copy failed.
- `move_back`: the inverse of `move`, for undo. Same rules, reversed.
- `rmdir_empty`: removes *empty* directories below `<download folder>\\<night>`, only
  when you ask for it. `os.rmdir` itself refuses a directory with anything in it.

- `set_status`: changes the `status` field of one target folder's own `target.json`
  (reopening a finished target that NINA is imaging again). Every other field is
  kept; written to a temporary file, then swapped in.

- `remove_duplicate`: removes a file from the download folder only when a byte-for-byte
  identical file is already in a revision's `.SessionData\\NIGHT_` folder (a copy
  re-synced into the download folder after its frame was moved, e.g. by robocopy), and only
  when you ask for it. The frame itself always survives: one copy stays in the session.

Nothing here deletes a frame's only copy. `tests/test_safety.py` allows file-mutating
calls in this module only.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import paths

SESSION_DATA = ".SessionData"


class UnsafeOperation(Exception):
    pass


def _resolve(p: str | Path) -> Path:
    return Path(os.path.abspath(p))


def _under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False


def _check_download(root: Path, download: Path) -> None:
    if why := paths.problem(root, download):
        raise UnsafeOperation(f"not a usable download folder ({download}): {why}")


def _check_download_src(download: Path, src: Path) -> None:
    if not _under(src, download):
        raise UnsafeOperation(f"source is not under {download}: {src}")


def _check_session_dst(root: Path, download: Path, dst: Path) -> None:
    if not _under(dst, root) or _under(dst, download):
        raise UnsafeOperation(f"destination is not inside a target folder of {root}: {dst}")
    parts = dst.relative_to(root).parts
    if SESSION_DATA not in parts or not any(p.startswith("NIGHT_") for p in parts):
        raise UnsafeOperation(f"destination is not in a .SessionData\\NIGHT_ folder: {dst}")
    if os.path.splitdrive(str(dst))[0].lower() != os.path.splitdrive(str(root))[0].lower():
        raise UnsafeOperation("destination is on another volume")


def move(root: Path, download: Path, src: str, dst: str) -> None:
    root, download, s, d = _resolve(root), _resolve(download), _resolve(src), _resolve(dst)
    _check_download(root, download)
    _check_download_src(download, s)
    _check_session_dst(root, download, d)
    if not s.is_file():
        raise FileNotFoundError(s)
    if d.exists():
        raise FileExistsError(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    os.rename(s, d)  # atomic on one volume; refuses an existing destination on Windows


def copy(root: Path, download: Path, src: str, dst: str, chunk: int = 4 * 1024 * 1024) -> None:
    root, download, s, d = _resolve(root), _resolve(download), _resolve(src), _resolve(dst)
    _check_download(root, download)
    _check_download_src(download, s)
    _check_session_dst(root, download, d)
    if d.exists():
        raise FileExistsError(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    part = d.with_name(f".{d.name}.{uuid.uuid4().hex[:8]}.part")
    created = False
    try:
        with open(s, "rb") as fin, open(part, "xb") as fout:
            created = True
            while block := fin.read(chunk):
                fout.write(block)
        os.utime(part, (s.stat().st_atime, s.stat().st_mtime))
        os.rename(part, d)
    except BaseException:
        if created and part.exists():
            os.remove(part)  # only the .part file this call created
        raise


def move_back(root: Path, download: Path, dst: str, src: str) -> None:
    """Undo of `move`: from the session back to where it came from, which must be in the current download folder."""
    root, download, d, s = _resolve(root), _resolve(download), _resolve(dst), _resolve(src)
    _check_download(root, download)
    _check_session_dst(root, download, d)
    _check_download_src(download, s)
    if not d.is_file():
        raise FileNotFoundError(d)
    if s.exists():
        raise FileExistsError(s)
    s.parent.mkdir(parents=True, exist_ok=True)
    os.rename(d, s)


def rmdir_empty(root: Path, download: Path, night_dir: str) -> list[str]:
    """Remove empty directories below (and including) one `<download folder>\\<night>` folder. Returns what was removed."""
    root, download, top = _resolve(root), _resolve(download), _resolve(night_dir)
    _check_download(root, download)
    if top.parent != download:
        raise UnsafeOperation(f"not a night folder directly under {download}: {top}")
    removed: list[str] = []
    for dirpath, dirnames, filenames in os.walk(top, topdown=False):
        if filenames:
            continue
        try:
            os.rmdir(dirpath)  # refuses a non-empty directory
            removed.append(dirpath)
        except OSError:
            continue
    return removed


def identical(a: Path, b: Path, chunk: int = 4 * 1024 * 1024) -> bool:
    """Byte-for-byte comparison (sizes first)."""
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def remove_duplicate(root: Path, download: Path, src: str, kept: str) -> None:
    """Remove `src` (in the download folder) because `kept` (in a session) is the very same frame, byte for byte."""
    root, download, s, k = _resolve(root), _resolve(download), _resolve(src), _resolve(kept)
    _check_download(root, download)
    _check_download_src(download, s)
    _check_session_dst(root, download, k)
    if s == k:
        raise UnsafeOperation("source and kept file are the same path")
    if not s.is_file() or not k.is_file():
        raise FileNotFoundError(s if not s.is_file() else k)
    if not identical(s, k):
        raise UnsafeOperation(f"not identical to the ingested frame, left in place: {s}")
    os.remove(s)  # the session copy remains


STATUSES = ("acquiring", "processed", "finished")


def set_status(root: Path, target_folder: str, status: str, defaults: dict | None = None) -> str | None:
    """Set `status` in `<root>/<target_folder>/target.json`; returns the previous status (None if there was none).

    Only a target folder directly under the data root (not the download folder, `.Calibration` or any dot-folder),
    only its `target.json`, only the `status` key. A missing file is created from `defaults`
    (catalog_id, name, revision) so the target keeps its identity.
    """
    import json

    if status not in STATUSES:
        raise UnsafeOperation(f"unknown status {status!r}")
    root = _resolve(root)
    folder = _resolve(root / target_folder)
    if folder.parent != root or folder.name.startswith(".") or not folder.is_dir():
        raise UnsafeOperation(f"not a target folder directly under {root}: {folder}")
    path = folder / "target.json"
    if path.is_file():
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise UnsafeOperation(f"{path} is not a JSON object")
    else:
        data = {k: v for k, v in (defaults or {}).items() if k in ("catalog_id", "name", "revision")}
    previous = data.get("status")
    data["status"] = status
    tmp = path.with_name(f".target.json.{uuid.uuid4().hex[:8]}.part")
    with open(tmp, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return previous
