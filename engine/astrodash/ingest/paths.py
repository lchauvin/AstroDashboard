"""Where NINA's frames land: the download folder (Settings → Live · Ingest), and what it may be.

By default `<data root>\\.Download`; any other folder must keep auto-ingest's guarantees:

- on the data root's drive: a frame only ever moves by a rename on one drive, never by a copy
  followed by a deletion (a share on another drive, or a UNC path, is refused);
- inside the data root, only a dot folder directly under it, like `.Download`: the Library takes
  any other folder there for a target, and `.Calibration`, `.Trash` and `.Dashboard` belong to others;
- never the data root itself or a folder holding it (ingest would walk the whole library).

Lexical checks only: `problem` does not look at the disk, so `fileops` can re-check every call.
Pure: no file is touched here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DOWNLOAD = ".Download"
RESERVED = {".calibration", ".trash", ".dashboard"}


def download_root(data_root: Path, settings: dict[str, Any] | None) -> Path:
    """The download folder: the setting `ingest.downloadDir`, else `<data root>\\.Download`."""
    custom = str(((settings or {}).get("ingest") or {}).get("downloadDir") or "").strip()
    return Path(custom) if custom else Path(data_root) / DOWNLOAD


def _drive(p: str) -> str:
    return os.path.splitdrive(p)[0].rstrip("\\/").casefold()


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child.casefold(), parent.casefold()]) == parent.casefold()
    except ValueError:  # different drives
        return False


def problem(data_root: Path, folder: Path) -> str | None:
    """Why `folder` cannot be the download folder, or None."""
    if not os.path.isabs(str(folder)) or not os.path.splitdrive(str(folder))[0]:
        return "it must be a full path, with its drive (e.g. G:\\NINA)"
    root, f = os.path.abspath(data_root), os.path.abspath(folder)
    if _drive(f) != _drive(root):
        return (f"it must be on {os.path.splitdrive(root)[0]} like the data root: auto-ingest only moves frames by renaming "
                "them on one drive, never by copying and deleting (for a share hosted on this PC, give its local path)")
    if _inside(root, f):
        return "it may not be the data root or a folder that holds it"
    if _inside(f, root):
        rel = Path(f).relative_to(root).parts
        if len(rel) != 1 or not rel[0].startswith(".") or rel[0].casefold() in RESERVED:
            return (f"inside the data root it must be a dot folder directly under it, like {DOWNLOAD} "
                    "(any other folder there is a target; .Calibration, .Trash and .Dashboard are taken)")
    return None
