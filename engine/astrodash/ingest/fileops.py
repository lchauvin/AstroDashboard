"""The only code in the engine that changes anything under the data root (PLAN §2.1, §5.1).

Every operation re-checks its paths, whatever the caller believes:

- `move`: a same-volume rename from `.Download\\…` into a revision's
  `.SessionData\\NIGHT_<date>\\…`. Never overwrites (the rename itself refuses
  an existing destination on Windows, and it is checked first anyway).
- `copy`: the same destinations, for flats shared by several targets. Written
  to a `.part` file opened with exclusive create, then renamed into place; the
  `.part` file is the only thing this module ever removes, and only when it
  created it and the copy failed.
- `move_back`: the inverse of `move`, for undo. Same rules, reversed.
- `rmdir_empty`: removes *empty* directories below `.Download\\<night>`, only
  when you ask for it. `os.rmdir` itself refuses a directory with anything in it.

Nothing here deletes a frame. `tests/test_safety.py` allows file-mutating
calls in this module only.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

DOWNLOAD = ".Download"
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


def _check_download_src(root: Path, src: Path) -> None:
    if not _under(src, root / DOWNLOAD):
        raise UnsafeOperation(f"source is not under {root / DOWNLOAD}: {src}")


def _check_session_dst(root: Path, dst: Path) -> None:
    if not _under(dst, root) or _under(dst, root / DOWNLOAD):
        raise UnsafeOperation(f"destination is not inside a target folder of {root}: {dst}")
    parts = dst.relative_to(root).parts
    if SESSION_DATA not in parts or not any(p.startswith("NIGHT_") for p in parts):
        raise UnsafeOperation(f"destination is not in a .SessionData\\NIGHT_ folder: {dst}")
    if os.path.splitdrive(str(dst))[0].lower() != os.path.splitdrive(str(root))[0].lower():
        raise UnsafeOperation("destination is on another volume")


def move(root: Path, src: str, dst: str) -> None:
    root, s, d = _resolve(root), _resolve(src), _resolve(dst)
    _check_download_src(root, s)
    _check_session_dst(root, d)
    if not s.is_file():
        raise FileNotFoundError(s)
    if d.exists():
        raise FileExistsError(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    os.rename(s, d)  # atomic on one volume; refuses an existing destination on Windows


def copy(root: Path, src: str, dst: str, chunk: int = 4 * 1024 * 1024) -> None:
    root, s, d = _resolve(root), _resolve(src), _resolve(dst)
    _check_download_src(root, s)
    _check_session_dst(root, d)
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


def move_back(root: Path, dst: str, src: str) -> None:
    """Undo of `move`: from the session back to where it came from in .Download."""
    root, d, s = _resolve(root), _resolve(dst), _resolve(src)
    _check_session_dst(root, d)
    _check_download_src(root, s)
    if not d.is_file():
        raise FileNotFoundError(d)
    if s.exists():
        raise FileExistsError(s)
    s.parent.mkdir(parents=True, exist_ok=True)
    os.rename(d, s)


def rmdir_empty(root: Path, night_dir: str) -> list[str]:
    """Remove empty directories below (and including) one `.Download\\<night>` folder. Returns what was removed."""
    root, top = _resolve(root), _resolve(night_dir)
    if top.parent != root / DOWNLOAD:
        raise UnsafeOperation(f"not a night folder directly under {root / DOWNLOAD}: {top}")
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
