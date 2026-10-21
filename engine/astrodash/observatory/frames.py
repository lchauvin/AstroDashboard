"""Tonight's frames (from NINA's own per-frame statistics), the newest file in .Download, the guide star.

Frame flags compare each light only with recent lights **of the same filter** (Ha legitimately
shows far fewer stars than L): HFR well above, or star count well below, the rolling median.
"""

from __future__ import annotations

import base64
import io
import os
import time
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

FRAME_SUFFIXES = (".fits", ".fit", ".fts", ".xisf")
HFR_JUMP = 0.25
STAR_DROP = 0.40


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def frame_record(raw: dict, index: int) -> dict:
    return {
        "index": index,
        "date": raw.get("Date"),
        "filename": raw.get("Filename"),
        "target": raw.get("TargetName") or None,
        "type": str(raw.get("ImageType") or "").upper() or None,
        "filter": raw.get("Filter") or None,
        "exposure": _num(raw.get("ExposureTime")),
        "hfr": _num(raw.get("HFR")),
        "stars": int(v) if (v := _num(raw.get("Stars"))) is not None else None,
        "temperature": _num(raw.get("Temperature")),
        "gain": _num(raw.get("Gain")),
        "median": _num(raw.get("Median")),
        "rms": raw.get("RmsText") or None,
    }


def _when(rec: dict) -> float:
    try:
        return datetime.fromisoformat(str(rec["date"]).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def flag(frames: list[dict]) -> list[dict]:
    """Oldest-first list → same list with `flags` set on lights (compared to the previous ≤10 of their filter)."""
    history: dict[str | None, list[dict]] = {}
    for f in frames:
        f["flags"] = []
        if f["type"] != "LIGHT":
            continue
        prev = history.setdefault(f["filter"], [])[-10:]
        hfrs = [p["hfr"] for p in prev if p["hfr"]]
        stars = [p["stars"] for p in prev if p["stars"]]
        if len(hfrs) >= 3 and f["hfr"]:
            jump = f["hfr"] / median(hfrs) - 1
            if jump > HFR_JUMP:
                f["flags"].append(f"HFR +{jump:.0%} (focus, clouds or tracking)")
        if len(stars) >= 3 and f["stars"] is not None:
            drop = 1 - f["stars"] / median(stars)
            if drop > STAR_DROP:
                f["flags"].append(f"stars −{drop:.0%} (clouds or haze?)")
        history[f["filter"]].append(f)
    return frames


def tonight(history: list[dict], hours: float = 14) -> list[dict]:
    """NINA image history (oldest first, position = image index) → tonight's records, newest first."""
    # A snapshot without a file (the Imaging panel with Save off) is not part of the night.
    recs = [frame_record(r, i) for i, r in enumerate(history)
            if isinstance(r, dict) and not (str(r.get("ImageType") or "").upper() == "SNAPSHOT" and not r.get("Filename"))]
    cutoff = time.time() - hours * 3600
    recs = [r for r in recs if _when(r) >= cutoff or not r["date"]]
    return list(reversed(flag(recs)))


def newest_file(root: Path, max_entries: int = 20_000, min_age_s: float = 2.0) -> dict | None:
    """Newest complete-looking frame under `root` (by mtime; anything written in the last moment is still landing)."""
    if not root.is_dir():
        return None
    best: tuple[float, str, int] | None = None
    seen = 0
    now = time.time()
    stack = [(str(root), 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            seen += 1
            if seen > max_entries:
                stack.clear()
                break
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < 5:
                        stack.append((e.path, depth + 1))
                    continue
                if not e.name.lower().endswith(FRAME_SUFFIXES):
                    continue
                st = e.stat()
            except OSError:
                continue
            if now - st.st_mtime >= min_age_s and (best is None or st.st_mtime > best[0]):
                best = (st.st_mtime, e.path, st.st_size)
    if best is None:
        return None
    return {"path": best[1], "name": Path(best[1]).name, "mtime": best[0], "size": best[2]}


def star_png(payload: dict) -> bytes:
    """PHD2 `get_star_image` (base64 little-endian uint16) → PNG, square-root stretched.

    Percentile endpoints, not min/max: one hot pixel would otherwise crush the star to grey. The
    square root lifts the wings, which is where elongation and bad focus show.
    """
    import numpy as np
    from PIL import Image

    w, h = int(payload.get("width") or 0), int(payload.get("height") or 0)
    raw = base64.b64decode(payload.get("pixels") or "")
    if w <= 0 or h <= 0 or len(raw) < w * h * 2:
        raise ValueError("PHD2 returned no star image")
    px = np.frombuffer(raw[: w * h * 2], dtype="<u2").astype(np.float32).reshape(h, w)
    lo, hi = np.percentile(px, (0.5, 99.9))
    scaled = np.sqrt(np.clip((px - lo) / (hi - lo), 0, 1)) if hi > lo else np.zeros_like(px)
    buf = io.BytesIO()
    Image.fromarray((scaled * 255 + 0.5).astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()
