"""PixInsight project files: the paths they reference, and how a plan re-points them. Read-only.

A `.pxiproject` is a folder; its `project.xosm` (XML) holds every path the
project uses — WBPP/ImageIntegration file lists, image sources — in two forms:
plain `G:/Astro/<target>/…` and `file:///G:/Astro/M%2031%20(…)/…` URLs (only
spaces encoded, as PixInsight writes them). The data blobs in `project.data`
carry no paths and are never touched.

Re-pointing rewrites only path prefixes:
- paths under the data root follow the plan's moves, in order;
- paths under a *former* root (e.g. `E:/Photo/Astro`, before the data moved to
  G:) whose next folder exists by name at the top of today's data root are
  moved to the data root — `C:/astro/ALIGNED` or a star database stay as they are;
- paths into what the plan sends to the trash are left alone (and counted).

Tested 2026-10-10 with PixInsight 1.9: an edited *sealed* project opens with a
"seal has been edited" warning and uses the new paths; saving it reseals it.
`apply.py` writes the result; this module never writes.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

XOSM = "project.xosm"
TOKEN = re.compile(r'(file:///)?([A-Za-z]:/[^"<>\r\n]*)')
IGNORED_TOPS = {".trash", ".dashboard"}

_scan_cache: dict[tuple[str, int, float], tuple[str, Counter]] = {}


def _encode(path: str) -> str:
    return path.replace("%", "%25").replace(" ", "%20")


def posix_root(root: Path) -> str:
    return str(root).replace("\\", "/").rstrip("/")


def scan(path: Path) -> tuple[str, Counter]:
    """sha1 of the file and its path references {(is_url, decoded path): count}; cached by size and mtime."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    hit = _scan_cache.get(key)
    if hit:
        return hit
    raw = path.read_bytes()
    refs: Counter = Counter()
    for m in TOKEN.finditer(raw.decode("utf-8", errors="replace")):
        refs[(bool(m[1]), unquote(m[2]) if m[1] else m[2])] += 1
    out = (hashlib.sha1(raw).hexdigest(), refs)
    _scan_cache[key] = out
    return out


class Mapper:
    """Maps an absolute forward-slash path to its new location after the plan, or None if unchanged."""

    def __init__(self, root: str, tops: set[str], moves: list[tuple[str, str]], trashed: list[str] = ()):
        self.root = root
        self.root_cf = root.casefold()
        self.tops = {t.casefold() for t in tops if t.casefold() not in IGNORED_TOPS}
        self.moves = [(s, d, s.casefold()) for s, d in moves]
        self.trashed = [t.casefold() for t in trashed]

    def rel(self, path: str) -> tuple[str | None, bool]:
        """(path relative to the data root, came from a former root)."""
        cf = path.casefold()
        if cf == self.root_cf or cf.startswith(self.root_cf + "/"):
            return path[len(self.root) + 1:], False
        parts = path.split("/")
        for j in range(1, len(parts)):
            if parts[j].casefold() in self.tops:
                return "/".join(parts[j:]), True
        return None, False

    def trashed_ref(self, path: str) -> bool:
        rel, _ = self.rel(path)
        if rel is None:
            return False
        cf = rel.casefold()
        return any(cf == t or cf.startswith(t + "/") for t in self.trashed)

    def __call__(self, path: str) -> str | None:
        rel, former = self.rel(path)
        if rel is None or self.trashed_ref(path):
            return None
        for src, dst, src_cf in self.moves:
            cf = rel.casefold()
            if cf == src_cf or cf.startswith(src_cf + "/"):
                rel = dst + rel[len(src):]
        new = f"{self.root}/{rel}" if rel else self.root
        return new if (former or new != path) else None


def plan_refs(refs: Counter, mapper: Mapper) -> dict[str, int]:
    """What re-pointing would do to one project: counts of references by outcome."""
    out = {"moved": 0, "formerRoot": 0, "trashed": 0, "skipped": 0}
    for (_is_url, path), n in refs.items():
        if mapper.trashed_ref(path):
            out["trashed"] += n
            continue
        new = mapper(path)
        if new is None:
            continue
        out["formerRoot" if mapper.rel(path)[1] else "moved"] += n
    return out


def rewrite(raw: bytes, mapper: Callable[[str], str | None]) -> tuple[bytes, int]:
    """The project file with every mapped path replaced; (new bytes, references changed)."""
    text = raw.decode("utf-8")
    changed = 0

    def sub(m: re.Match) -> str:
        nonlocal changed
        if m[1]:
            path = unquote(m[2])
            if _encode(path) != m[2]:  # not PixInsight's encoding: leave it exactly as it is
                return m[0]
            new = mapper(path)
            if new is None:
                return m[0]
            changed += 1
            return m[1] + _encode(new)
        new = mapper(m[2])
        if new is None:
            return m[0]
        changed += 1
        return new

    return TOKEN.sub(sub, text).encode("utf-8"), changed


def top_level(root: Path) -> set[str]:
    try:
        return {e.name for e in os.scandir(root) if e.is_dir()}
    except OSError:
        return set()
