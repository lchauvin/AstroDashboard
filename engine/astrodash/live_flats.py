"""Tonight's flats against the filters of NINA's imaging sequence (Live › status strip).

Per filter of the sequence's LIGHT exposures: whether tonight has flats for it, and whether they were shot
in the readout mode flats must use (config `flat_readout`, "Full Resolution": this sensor's flats misbehave
in HDR). Flats are read where they can be tonight: the revisions' NIGHT_<tonight> folders (once ingested)
and the ingest plan's flats still waiting in the download folder. Headers are cached by path, size and mtime,
so a poll only reads the frames that are new.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .frames import canonical_filter, normalize_kind
from .headers import IMAGE_SUFFIXES, read_header
from .ingest.planner import evening_of

_cache: dict[str, tuple[int, float, dict[str, Any]]] = {}


def _header(path: str) -> dict[str, Any] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit[2]
    try:
        header = read_header(Path(path))
    except Exception:  # noqa: BLE001 — a frame still being written, or not an image
        return None
    _cache[path] = (st.st_size, st.st_mtime, header)
    return header


def sequence_filters(steps: list[dict[str, Any]], aliases: dict[str, str]) -> list[str]:
    """Filters of the sequence's LIGHT exposures, in sequence order. A step without an image type counts
    when no step has one (older plugin versions); flat steps never do."""
    typed = any(s.get("info", {}).get("ImageType") for s in steps)
    out: list[str] = []
    for s in steps:
        info = s.get("info") or {}
        if not info.get("Filter") or "flat" in str(s.get("name", "")).casefold():
            continue
        kind = str(info.get("ImageType") or "").upper()
        if (typed and kind != "LIGHT") or (not typed and not info.get("ExposureTime")):
            continue
        f = canonical_filter(info["Filter"], aliases)
        if f and f not in out:
            out.append(f)
    return out


def _night_flats(session_roots: Iterable[str], tonight: str) -> Iterable[str]:
    for root in session_roots:
        night = os.path.join(root, f"NIGHT_{tonight}")
        if not os.path.isdir(night):
            continue
        for dirpath, _dirs, files in os.walk(night):
            if "FLAT" not in {p.upper() for p in Path(dirpath).relative_to(night).parts}:
                continue
            for name in files:
                if os.path.splitext(name)[1].lower() in IMAGE_SUFFIXES:
                    yield os.path.join(dirpath, name)


def tonight_flats(
    *,
    tonight: str,
    steps: list[dict[str, Any]],
    light_filters: list[str],
    session_roots: Iterable[str],
    waiting: Iterable[str],
    aliases: dict[str, str],
    want_readout: str | None,
) -> dict[str, Any]:
    """→ {"night", "source", "wantReadout", "filters": [{filter, level, frames, readouts, wrong}], "other": [...]}.

    level: "ok" (flats, all in `want_readout`), "warn" (some in another readout mode, or no readout in the
    header), "bad" (no flats tonight). `light_filters` (tonight's lights) stand in when no sequence is loaded.
    """
    filters = sequence_filters(steps, aliases)
    source = "sequence"
    if not filters:
        filters, source = list(dict.fromkeys(light_filters)), "lights"
    night = date.fromisoformat(tonight)
    by_filter: dict[str, list[str | None]] = {}
    seen: set[str] = set()
    for path in [*_night_flats(session_roots, tonight), *waiting]:
        # One frame, once: robocopy re-copies an ingested flat into the download folder (same name and size).
        try:
            key = f"{os.path.basename(path).casefold()}|{os.path.getsize(path)}"
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        h = _header(path)
        if h is None or normalize_kind(h.get("IMAGETYP"), None, os.path.basename(path)) != "FLAT":
            continue
        when = evening_of(h.get("DATE-LOC")) or evening_of(h.get("DATE-OBS"))
        if when is not None and when != night:
            continue
        f = canonical_filter(h.get("FILTER"), aliases) or "—"
        readout = h.get("READOUTM")
        by_filter.setdefault(f, []).append(str(readout).strip() if readout not in (None, "") else None)

    def row(f: str) -> dict[str, Any]:
        readouts = by_filter.get(f, [])
        modes = sorted({r for r in readouts if r})
        wrong = sorted({r for r in modes if want_readout and r.casefold() != want_readout.casefold()})
        unknown = sum(1 for r in readouts if r is None)
        level = "bad" if not readouts else "warn" if wrong or (want_readout and unknown) else "ok"
        return {"filter": f, "level": level, "frames": len(readouts), "readouts": modes, "wrong": wrong, "unknownReadout": unknown}

    return {
        "night": tonight,
        "source": source,
        "wantReadout": want_readout,
        "filters": [row(f) for f in filters],
        # Flats tonight for filters the sequence does not use (shown for completeness).
        "other": [row(f) for f in sorted(by_filter) if f not in filters],
    }
