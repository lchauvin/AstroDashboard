"""Light-leak finder: compare sets of darks taken before and after covering part of the rig.

A set is the dark frames of one folder at one setting (exposure, set temperature,
readout, gain, offset, binning). Each frame is reduced to a 4×4-binned image whose
blocks are outlier-rejected means: the raw frames are integer ADU with ~1.4 ADU
of read noise, so plain medians are quantized and blind to sub-ADU changes.

Per set: per-frame median / mean / σ, a trimmed-mean master, and two half masters
(even and odd frames). The half masters' difference is pure noise plus whatever
changed during the set, so it sets the noise floor of every structure metric in
a comparison. Leaks show up as structure in comparison − baseline: a gradient
across the frame, a bright edge, a patch of glow, or a level shift.

Images are read-only; arrays live in var/leaks/.
"""

from __future__ import annotations

import io
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .frames import normalize_kind, parse_time
from .headers import IMAGE_SUFFIXES, read_header
from .preview import _load

BIN = 4                 # sensor px per binned px
CELL = 16               # binned px per coarse cell (large-scale maps and the plane fit)
MAX_FRAMES = 60         # evenly spaced frames per set beyond this
BROWSE_MAX_FILES = 5000
BROWSE_DEPTH = 3
SMOOTH_SIGMA = 1.5      # binned px, master images
DIFF_SIGMA = 4.0        # binned px, difference images: leaks are large-scale, the noise is not
# A change counts when it is both this many noise σ and at least this many ADU: the noise
# floor from the half masters is tiny (hundredths of an ADU), so tiny bias drifts would pass.
MIN_Z = 4.0
MIN_LEVEL_ADU = 0.1
MIN_STRUCT_ADU = 0.1
MIN_NOISE_ADU = 0.02
RESIDUAL_RATIO = 1.5
# Frame types a test can use: darks, and lights taken with the scope capped (phase 1 shoots
# Luminance frames with a flashlight on one spot). Flats never.
FRAME_KINDS = {"DARK", "BIAS", "DARKFLAT", "LIGHT", None}
MAX_CHECK_FRAMES = 120


# ---------------------------------------------------------------------------- frames and sets

def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def frame_settings(h: dict[str, Any], gain_ignored: Callable[[str | None], bool]) -> dict[str, Any]:
    exposure = _num(h.get("EXPOSURE"))
    if exposure is None:
        exposure = _num(h.get("EXPTIME"))
    ccd = _num(h.get("CCD-TEMP"))
    set_temp = _num(h.get("SET-TEMP"))
    if set_temp is None and ccd is not None:
        set_temp = float(round(ccd))
    readout = str(h["READOUTM"]).strip() if h.get("READOUTM") not in (None, "") else None
    ignore = gain_ignored(readout)
    binning = _num(h.get("XBINNING"))
    t = parse_time(h.get("DATE-OBS"))
    filt = h.get("FILTER")
    return {
        "filter": str(filt).strip() or None if filt not in (None, "") else None,
        "exposure": round(exposure, 6) if exposure is not None else None,
        "setTemp": set_temp,
        "readout": readout,
        "gain": None if ignore else _num(h.get("GAIN")),
        "offset": None if ignore else _num(h.get("OFFSET")),
        "binning": int(binning) if binning is not None else None,
        "instrume": str(h["INSTRUME"]).strip() if h.get("INSTRUME") else None,
        "ccdTemp": ccd,
        "time": t.isoformat() if t else None,
    }


SETTING_KEYS = ("exposure", "setTemp", "readout", "gain", "offset", "binning")


def matches(s: dict[str, Any], want: dict[str, Any]) -> bool:
    def close(a, b, tol):
        return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tol)

    return (
        close(s["exposure"], want.get("exposure"), 0.01)
        and close(s["setTemp"], want.get("setTemp"), 0.5)
        and (s["readout"] or "").casefold() == (want.get("readout") or "").casefold()
        and close(s["gain"], want.get("gain"), 0)
        and close(s["offset"], want.get("offset"), 0)
        and (want.get("binning") is None or s["binning"] == want.get("binning"))
        and (not want.get("filter") or (s["filter"] or "").casefold() == want["filter"].casefold())
    )


def _image_files(folder: Path, depth: int) -> list[Path]:
    out: list[Path] = []
    base = len(folder.parts)
    for dirpath, dirnames, filenames in os.walk(folder):
        if len(Path(dirpath).parts) - base >= depth:
            dirnames[:] = []
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "_")))
        for f in sorted(filenames):
            if Path(f).suffix.lower() in IMAGE_SUFFIXES:
                out.append(Path(dirpath) / f)
                if len(out) >= BROWSE_MAX_FILES:
                    return out
    return out


def _frame_header(path: Path) -> dict[str, Any] | None:
    try:
        h = read_header(path)
    except Exception:  # noqa: BLE001
        return None
    kind = normalize_kind(h.get("IMAGETYP"), None, path.name)
    return h if kind in FRAME_KINDS else None


def find_groups(folder: Path, gain_ignored: Callable[[str | None], bool]) -> dict[str, Any]:
    """Dark (and capped light) frames under `folder`, a few levels deep, grouped by subfolder, type, filter and setting."""
    files = _image_files(folder, BROWSE_DEPTH)
    groups: dict[tuple, dict[str, Any]] = {}
    for p in files:
        h = _frame_header(p)
        if h is None:
            continue
        s = frame_settings(h, gain_ignored)
        kind = normalize_kind(h.get("IMAGETYP"), None, p.name) or "unknown"
        key = (str(p.parent), kind, s["filter"]) + tuple(s[k] for k in SETTING_KEYS)
        g = groups.setdefault(key, {
            "folder": str(p.parent), "kind": kind.lower(), "filter": s["filter"], **{k: s[k] for k in SETTING_KEYS},
            "instrume": s["instrume"], "frames": 0, "first": None, "last": None,
        })
        g["frames"] += 1
        if s["time"]:
            g["first"] = min(g["first"] or s["time"], s["time"])
            g["last"] = max(g["last"] or s["time"], s["time"])
    rows = sorted(groups.values(), key=lambda g: (g["folder"].casefold(), g["exposure"] or 0, -(g["setTemp"] or 0)))
    return {"folder": str(folder), "scanned": len(files), "truncated": len(files) >= BROWSE_MAX_FILES, "groups": rows}


def set_files(folder: Path, want: dict[str, Any], gain_ignored) -> list[tuple[Path, dict[str, Any]]]:
    """Frames of one set: directly in `folder`, matching the setting, oldest first."""
    out = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        h = _frame_header(p)
        if h is None:
            continue
        s = frame_settings(h, gain_ignored)
        if matches(s, want):
            out.append((p, s))
    out.sort(key=lambda x: (x[1]["time"] or "", x[0].name))
    return out


# ---------------------------------------------------------------------------- reduction

def _cmean(x: np.ndarray, k: float = 5.0) -> float:
    """Mean of the values within k robust σ of the median. Continuous where a median of ADU-step data is not."""
    x = np.asarray(x, dtype=np.float64).ravel()
    x = x[np.isfinite(x)]
    med = np.median(x)
    s = np.median(np.abs(x - med)) * 1.4826
    if s <= 0:
        return float(x.mean())
    return float(x[np.abs(x - med) <= k * s].mean())


def _combine(arr: np.ndarray) -> np.ndarray:
    """Per-pixel mean across frames without the highest and lowest (cosmic rays, a stray bright frame)."""
    if arr.shape[0] >= 5:
        return np.sort(arr, axis=0)[1:-1].mean(axis=0).astype(np.float32)
    return arr.mean(axis=0).astype(np.float32)


def _robust_std(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    med = np.median(x)
    return float(np.median(np.abs(x - med)) * 1.4826)


def bin_frame(img: np.ndarray) -> tuple[np.ndarray, dict[str, float]]:
    """4×4 blocks → mean of the pixels within 6σ of the block median (hot pixels and cosmics out,
    glow kept: the threshold follows the block). Plus whole-frame statistics."""
    h, w = img.shape
    a = img[: h // BIN * BIN, : w // BIN * BIN]
    sub = a[::2, ::2]
    med0 = float(np.median(sub))
    sigma0 = max(float(np.median(np.abs(sub - med0))) * 1.4826, 0.5)
    # Pixel-to-pixel noise from neighbour differences: blind to gradients and glow, not to their shot noise.
    pairs = a[::2, 1::2] - a[::2, 0::2]
    keep = pairs[np.abs(pairs - np.median(pairs)) <= 6 * sigma0 * math.sqrt(2)]
    noise = float(keep.std() / math.sqrt(2)) if keep.size else float("nan")

    b = a.reshape(h // BIN, BIN, w // BIN, BIN).transpose(0, 2, 1, 3).reshape(h // BIN, w // BIN, BIN * BIN)
    bmed = np.median(b, axis=2, keepdims=True)
    mask = np.abs(b - bmed) <= 6 * sigma0
    binned = (np.where(mask, b, 0).sum(axis=2) / np.maximum(mask.sum(axis=2), 1)).astype(np.float32)
    return binned, {
        # median: whole ADU in the raw frame, 1/16 ADU steps here. level: continuous, and what comparisons use.
        "median": float(np.median(binned)),
        "level": _cmean(binned),
        "mean": float(img.mean()),
        "std": float(img.std()),
        "noise": noise,
    }


def _coarse(img: np.ndarray) -> np.ndarray:
    h, w = img.shape
    ch, cw = h // CELL, w // CELL
    a = img[: ch * CELL, : cw * CELL].reshape(ch, CELL, cw, CELL)
    return a.mean(axis=(1, 3))


def _design(shape: tuple[int, int]) -> np.ndarray:
    ch, cw = shape
    v, u = np.mgrid[0:ch, 0:cw]
    u = (u.ravel() + 0.5) / cw - 0.5
    v = (v.ravel() + 0.5) / ch - 0.5
    return np.column_stack([np.ones_like(u), u, v])


SIDES = ["right", "bottom-right", "bottom", "bottom-left", "left", "top-left", "top", "top-right"]


def side_name(dx: float, dy: float) -> str:
    """Direction of (dx right, dy down) as shown in the images (row 0 at the top)."""
    ang = math.degrees(math.atan2(dy, dx)) % 360
    return SIDES[int(((ang + 22.5) % 360) // 45)]


def _bands(shape: tuple[int, int]) -> dict[str, tuple[slice, slice]]:
    h, w = shape
    bh, bw = max(2, round(h * 0.04)), max(2, round(w * 0.04))
    return {
        "top": (slice(0, bh), slice(None)),
        "bottom": (slice(h - bh, h), slice(None)),
        "left": (slice(None), slice(0, bw)),
        "right": (slice(None), slice(w - bw, w)),
    }


def _profile(values: np.ndarray, points: int) -> list[float]:
    n = len(values)
    edges = np.linspace(0, n, points + 1).astype(int)
    return [round(float(values[a:b].mean()), 4) for a, b in zip(edges[:-1], edges[1:]) if b > a]


def structure(img: np.ndarray) -> dict[str, Any]:
    """Large-scale shape of a master or a difference: plane gradient, edge bands, 3×3 zones, residual patchiness."""
    coarse = _coarse(img)
    X = _design(coarse.shape)
    z = coarse.ravel()
    coef, *_ = np.linalg.lstsq(X, z, rcond=None)
    resid = z - X @ coef
    b = _bands(img.shape)
    h, w = img.shape
    # The plane at full resolution: edge bands are measured against it, so a gradient is not an edge.
    plane = coef[0] + coef[1] * ((np.arange(w) + 0.5) / w - 0.5)[None, :] + coef[2] * ((np.arange(h) + 0.5) / h - 0.5)[:, None]
    zones = [
        [round(_cmean(img[r * h // 3:(r + 1) * h // 3, c * w // 3:(c + 1) * w // 3]), 4) for c in range(3)]
        for r in range(3)
    ]
    return {
        "level": round(_cmean(img), 4),
        "gradient": {
            "dx": round(float(coef[1]), 4), "dy": round(float(coef[2]), 4),
            "amp": round(float(math.hypot(coef[1], coef[2])), 4), "side": side_name(coef[1], coef[2]),
        },
        "edges": {k: round(_cmean(img[b[k]] - plane[b[k]]), 4) for k in ("top", "bottom", "left", "right")},
        "zones": zones,
        "residualRms": round(float(resid.std()), 4),
        # Most of the frame: what a local glow is seen against (the level is pulled toward the glow).
        "background": round(float(np.median(coarse)), 4),
        "rows": _profile(img.mean(axis=1), 120),
        "cols": _profile(img.mean(axis=0), 160),
    }


def _fit_line(t: list[float], y: list[float]) -> tuple[float, float]:
    """Slope and its standard error."""
    n = len(t)
    if n < 3:
        return 0.0, float("inf")
    ta, ya = np.array(t), np.array(y)
    tm = ta - ta.mean()
    sxx = float((tm ** 2).sum())
    if sxx <= 0:
        return 0.0, float("inf")
    slope = float((tm * (ya - ya.mean())).sum() / sxx)
    resid = ya - ya.mean() - slope * tm
    se = math.sqrt(float((resid ** 2).sum()) / (n - 2) / sxx)
    return slope, se


def _reduce(used: list[tuple[Path, dict[str, Any]]], progress: Callable[[int, int], None]) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Per-frame statistics and the stack of binned frames."""
    stack: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    shape = None
    for i, (path, s) in enumerate(used):
        progress(i, len(used))
        img = _load(path)
        if shape is None:
            shape = img.shape
        elif img.shape != shape:
            raise ValueError(f"{path.name} is {img.shape[1]}×{img.shape[0]}, the first frame is {shape[1]}×{shape[0]}")
        binned, st = bin_frame(img)
        stack.append(binned)
        rows.append({"file": path.name, "time": s["time"], "ccdTemp": s["ccdTemp"], "width": int(shape[1]), "height": int(shape[0]),
                     **{k: round(v, 4) for k, v in st.items()}})
    progress(len(used), len(used))
    return rows, np.stack(stack)


def analyse_set(files: list[tuple[Path, dict[str, Any]]], progress: Callable[[int, int], None] = lambda i, n: None
                ) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    if len(files) > MAX_FRAMES:
        idx = np.linspace(0, len(files) - 1, MAX_FRAMES).round().astype(int)
        used = [files[i] for i in sorted(set(idx))]
    else:
        used = files
    if len(used) < 2:
        raise ValueError("a set needs at least 2 frames")
    rows, arr = _reduce(used, progress)
    shape = (rows[0]["height"], rows[0]["width"])
    # Each frame's uniform offset out (this camera's bias level steps by ~0.5 ADU between frames):
    # the masters carry shape, at the set's mean level, and the level is compared frame by frame.
    levels = np.array([r["level"] for r in rows], dtype=np.float32)
    arr -= (levels - levels.mean())[:, None, None]
    master = _combine(arr)
    half_a = _combine(arr[0::2])
    half_b = _combine(arr[1::2])
    del arr

    first = used[0][1]
    times = [datetime.fromisoformat(r["time"]) for r in rows if r["time"]]
    t0 = times[0] if times else None
    hours = [(datetime.fromisoformat(r["time"]) - t0).total_seconds() / 3600 for r in rows] if len(times) == len(rows) else []
    meds = [r["level"] for r in rows]
    drift = None
    if hours and hours[-1] > 0:
        slope, se = _fit_line(hours, meds)
        total = slope * hours[-1]
        drift = {"adu": round(total, 4), "hours": round(hours[-1], 3), "z": round(abs(slope / se), 1) if se and math.isfinite(se) and se > 0 else None}
        drift["significant"] = bool(abs(total) >= MIN_LEVEL_ADU and (drift["z"] is None or drift["z"] >= MIN_Z))
    temps = [r["ccdTemp"] for r in rows if r["ccdTemp"] is not None]
    hd = half_a - half_b
    summary = {
        "frames": len(rows),
        "available": len(files),
        "width": int(shape[1]), "height": int(shape[0]),
        "settings": {k: first[k] for k in SETTING_KEYS} | {"instrume": first["instrume"]},
        "first": rows[0]["time"], "last": rows[-1]["time"],
        "ccdTemp": {"median": float(np.median(temps)), "min": min(temps), "max": max(temps)} if temps else None,
        "median": round(float(np.median([r["median"] for r in rows])), 4),
        "level": round(float(np.mean(meds)), 4),
        "levelScatter": round(float(np.std(meds, ddof=1)), 4),
        "mean": round(float(np.mean([r["mean"] for r in rows])), 4),
        "std": round(float(np.median([r["std"] for r in rows])), 4),
        "noise": round(float(np.median([r["noise"] for r in rows])), 4),
        "drift": drift,
        "structure": structure(master),
        # Noise of the half-master difference, per binned px, per coarse cell, and after the display smoothing.
        "halfNoise": _noise(hd),
        "perFrame": rows,
    }
    return summary, {"master": master, "halfA": half_a, "halfB": half_b}


def _smooth(img: np.ndarray, sigma: float = SMOOTH_SIGMA) -> np.ndarray:
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(img, sigma, mode="nearest")


def _noise(hd: np.ndarray) -> dict[str, float]:
    return {"px": round(_robust_std(hd), 5), "cell": round(_robust_std(_coarse(hd)), 5), "smooth": round(_robust_std(_smooth(hd, DIFF_SIGMA)), 5)}


# ---------------------------------------------------------------------------- comparison

def _se(rows: list[dict[str, Any]], key: str) -> tuple[float, float]:
    """Mean and its standard error. Not the median: frame levels can be two-valued (bias steps),
    and a median then jumps by a whole step when the count of high frames crosses half."""
    v = np.array([r[key] for r in rows])
    return float(v.mean()), float(v.std(ddof=1) / math.sqrt(len(v))) if len(v) > 1 else float("inf")


def _z(d: float, se: float) -> float | None:
    return round(abs(d) / se, 1) if se and math.isfinite(se) and se > 0 else None


def _sig(d: float, z: float | None, floor: float) -> bool:
    return abs(d) >= floor and (z is None or z >= MIN_Z)


def _adu(v: float) -> str:
    return f"{abs(v):.2f} ADU"


def compare(base: dict[str, Any], comp: dict[str, Any], a_base: dict[str, np.ndarray], a_comp: dict[str, np.ndarray]) -> dict[str, Any]:
    """Comparison − baseline: level, noise, and the structure of the difference against the half-master noise."""
    notes: list[dict[str, str]] = []
    if a_base["master"].shape != a_comp["master"].shape:
        raise ValueError(f"the sets have different frame sizes ({base['width']}×{base['height']} vs {comp['width']}×{comp['height']})")
    sb, sc = base["settings"], comp["settings"]
    diffs = [k for k in SETTING_KEYS if sb.get(k) != sc.get(k)]
    if diffs:
        names = {"exposure": "exposure", "setTemp": "set temperature", "readout": "readout mode", "gain": "gain", "offset": "offset", "binning": "binning"}
        notes.append({"level": "bad", "text": "The sets differ in " + ", ".join(names[k] for k in diffs)
                      + ": dark current and bias differ too, so a level change says nothing about light."})
    tb, tc = base.get("ccdTemp"), comp.get("ccdTemp")
    if tb and tc and abs(tb["median"] - tc["median"]) > 0.5:
        notes.append({"level": "warn", "text": f"Sensor temperature differs by {abs(tb['median'] - tc['median']):.1f} °C between the sets; dark current differs with it."})
    for name, s in (("baseline", base), ("comparison", comp)):
        if s["frames"] < 6:
            notes.append({"level": "info", "text": f"The {name} has only {s['frames']} frames: the noise floor is a rough estimate. 10 or more is better."})
        d = s.get("drift")
        scatter = s.get("levelScatter") or 0
        if scatter >= MIN_LEVEL_ADU and not (d and d["significant"]):
            notes.append({"level": "info", "text": f"The {name}'s frame levels scatter by ±{scatter:.2f} ADU (the bias level steps between frames). "
                          "The level comparison allows for it; the structure comparison is not affected."})
        if d and d["significant"]:
            way = "brightened" if d["adu"] > 0 else "darkened"
            notes.append({"level": "warn", "text": f"The {name} frames {way} by {_adu(d['adu'])} over {d['hours'] * 60:.0f} min. Changing ambient light (dawn, room lights) "
                          "reaching the sensor does this — or the camera was still settling."})

    mb, seb = _se(base["perFrame"], "level")
    mc, sec = _se(comp["perFrame"], "level")
    d_level = mc - mb
    se_level = math.hypot(seb, sec)
    nb, nseb = _se(base["perFrame"], "noise")
    nc, nsec = _se(comp["perFrame"], "noise")
    d_noise = nc - nb
    se_noise = math.hypot(nseb, nsec)

    D = a_comp["master"] - a_base["master"]
    st = structure(D)
    hb, hc = base["halfNoise"], comp["halfNoise"]
    noise = {k: 0.5 * math.hypot(hb[k], hc[k]) for k in ("px", "cell", "smooth")}
    coarse_shape = _coarse(D).shape
    cov = np.linalg.inv(_design(coarse_shape).T @ _design(coarse_shape))
    se_grad = noise["cell"] * math.sqrt((cov[1, 1] + cov[2, 2]) / 2)
    bands = _bands(D.shape)
    se_edge = {k: noise["px"] * math.sqrt(math.pi / 2 / D[bands[k]].size) for k in ("top", "bottom", "left", "right")}

    g = st["gradient"]
    grad_z = _z(g["amp"], se_grad)
    edge_z = {k: _z(v, se_edge[k]) for k, v in st["edges"].items()}
    residual_ratio = st["residualRms"] / noise["cell"] if noise["cell"] > 0 else None

    level_sig = _sig(d_level, _z(d_level, se_level), MIN_LEVEL_ADU)
    noise_sig = _sig(d_noise, _z(d_noise, se_noise), MIN_NOISE_ADU)
    grad_sig = _sig(g["amp"], grad_z, MIN_STRUCT_ADU)
    edge_sig = {k: _sig(v, edge_z[k], MIN_STRUCT_ADU) for k, v in st["edges"].items()}
    resid_sig = bool(residual_ratio and residual_ratio >= RESIDUAL_RATIO and st["residualRms"] >= MIN_STRUCT_ADU / 2)

    better, worse = [], []
    findings: list[dict[str, Any]] = []
    if level_sig:
        (better if d_level < 0 else worse).append("level")
        findings.append({"level": "ok" if d_level < 0 else "bad", "text": f"The comparison is {'darker' if d_level < 0 else 'brighter'} overall by {_adu(d_level)} (frame levels)."})
    if noise_sig:
        (better if d_noise < 0 else worse).append("noise")
        findings.append({"level": "ok" if d_noise < 0 else "warn", "text": f"Frame noise {'dropped' if d_noise < 0 else 'rose'} by {_adu(d_noise)} — extra light also adds shot noise."})
    if grad_sig:
        gb, gc = base["structure"]["gradient"], comp["structure"]["gradient"]
        if gc["amp"] < gb["amp"] - g["amp"] / 2:
            better.append("gradient")
            findings.append({"level": "ok", "text": f"The gradient across the frame shrank from {gb['amp']:.2f} to {gc['amp']:.2f} ADU "
                                                    f"(the baseline was brighter toward the {gb['side']})."})
        elif gc["amp"] > gb["amp"] + g["amp"] / 2:
            worse.append("gradient")
            findings.append({"level": "bad", "text": f"A gradient grew from {gb['amp']:.2f} to {gc['amp']:.2f} ADU: the comparison is brighter toward the {g['side']}."})
        else:
            findings.append({"level": "warn", "text": f"The gradient turned: the comparison is {_adu(g['amp'])} brighter toward the {g['side']} than the baseline, "
                                                      f"at a similar size ({gb['amp']:.2f} → {gc['amp']:.2f} ADU)."})
    for side, v in st["edges"].items():
        if edge_sig[side]:
            (better if v < 0 else worse).append(f"edge-{side}")
            findings.append({"level": "ok" if v < 0 else "bad",
                             "text": f"The {side} edge is {_adu(v)} {'darker' if v < 0 else 'brighter'} than before, beyond the gradient."})
    if resid_sig:
        findings.append({"level": "warn", "text": f"Structure beyond a straight gradient changed — glow, curvature or patches, {residual_ratio:.1f}× the noise floor: look at the difference image."})

    if better and not worse:
        verdict, headline = "better", "Darker or flatter: this change seems to block light."
    elif worse and not better:
        verdict, headline = "worse", "Brighter or more structured: more light reaches the sensor (or the conditions changed)."
    elif better and worse:
        verdict, headline = "mixed", "Mixed: some parts got darker, others brighter. Look at the difference image."
    elif resid_sig or grad_sig:
        verdict, headline = "changed", "The pattern changed without a clear brighter/darker trend."
    else:
        verdict, headline = "same", "No measurable difference: this change did not fix a leak (or there was no leak where you covered)."

    # Shared display ranges for both masters (smoothed, so structure, not noise, sets them):
    # as they are, and each with its own level taken out (shape only).
    sm_b, sm_c = _smooth(a_base["master"]), _smooth(a_comp["master"])
    lo = float(min(np.percentile(sm_b, 0.5), np.percentile(sm_c, 0.5)))
    hi = float(max(np.percentile(sm_b, 99.5), np.percentile(sm_c, 99.5)))
    cb, cc = sm_b - float(np.median(sm_b)), sm_c - float(np.median(sm_c))
    lo_c = float(min(np.percentile(cb, 0.5), np.percentile(cc, 0.5)))
    hi_c = float(max(np.percentile(cb, 99.5), np.percentile(cc, 99.5)))
    sd = _smooth(D, DIFF_SIGMA)
    return {
        "verdict": verdict,
        "headline": headline,
        "findings": findings,
        "notes": notes,
        "level": {"base": round(mb, 4), "comp": round(mc, 4), "delta": round(d_level, 4), "z": _z(d_level, se_level), "significant": level_sig},
        "noise": {"base": round(nb, 4), "comp": round(nc, 4), "delta": round(d_noise, 4), "z": _z(d_noise, se_noise), "significant": noise_sig},
        "diff": {
            **st,
            "gradient": {**g, "z": grad_z, "significant": grad_sig},
            "edgeZ": edge_z,
            "edgeSignificant": edge_sig,
            "residualRatio": round(residual_ratio, 2) if residual_ratio else None,
            "residualSignificant": resid_sig,
            "noise": {k: round(v, 5) for k, v in noise.items()},
        },
        "display": {
            "lo": round(lo, 3), "hi": round(hi, 3), "loCentered": round(lo_c, 3), "hiCentered": round(hi_c, 3),
            "structureLimit": round(max(5 * noise["smooth"], 0.01), 4),
            "fitLimit": round(max(float(np.percentile(np.abs(sd - np.median(sd)), 99.5)), 5 * noise["smooth"], 0.01), 4),
            "absoluteLimit": round(max(float(np.percentile(np.abs(sd), 99.5)), 0.01), 4),
        },
    }


# ---------------------------------------------------------------------------- phase 1: spot checks
#
# Single frames, each with a flashlight on one spot of the rig, against reference frames taken
# without it. The flashlight moves, so a leak is mostly extra light overall, not a steady
# gradient. Light raises the level AND the pixel noise (its own shot noise); this camera's
# bias steps raise the level only. So the noise is the tell, the level the size, and any
# non-uniform pattern beyond the noise a hint of where the light gets in.

MIN_SPOT_NOISE_ADU = 0.01
SPOT_STRUCT_RATIO = 2.0
MIN_SPOT_STRUCT_ADU = 0.02
ZONE_NAMES = [["top-left", "top", "top-right"], ["left", "centre", "right"], ["bottom-left", "bottom", "bottom-right"]]


def analyse_check(files: list[tuple[Path, dict[str, Any]]], progress: Callable[[int, int], None] = lambda i, n: None
                  ) -> tuple[list[dict[str, Any]], np.ndarray]:
    if len(files) < 2:
        raise ValueError("a spot check needs at least 2 frames (one of them without the flashlight)")
    if len(files) > MAX_CHECK_FRAMES:
        raise ValueError(f"{len(files)} frames: a spot check takes at most {MAX_CHECK_FRAMES}")
    return _reduce(files, progress)


def _noise_error(row: dict[str, Any]) -> float:
    """Standard error of a frame's noise estimate (neighbour pairs on every other row)."""
    pairs = max(1, (row["height"] // 2) * (row["width"] // 2))
    return row["noise"] / math.sqrt(2 * pairs)


def auto_reference(rows: list[dict[str, Any]]) -> list[int]:
    """The darkest frames by pixel noise: what a frame with no flashlight looks like."""
    noise = [r["noise"] for r in rows]
    low = min(noise)
    tol = max(MIN_SPOT_NOISE_ADU, 4 * math.sqrt(2) * _noise_error(rows[int(np.argmin(noise))]))
    return [i for i, v in enumerate(noise) if v <= low + tol]


def evaluate_check(rows: list[dict[str, Any]], stack: np.ndarray, roles: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Every frame against the reference master. Returns (evaluation, arrays for the images)."""
    n = len(rows)
    ref = [i for i in range(n) if roles[i] == "reference"]
    auto = not ref
    if auto:
        ref = auto_reference(rows)
    lv = np.array([rows[i]["level"] for i in ref])
    nz = np.array([rows[i]["noise"] for i in ref])
    ref_level, ref_noise = float(lv.mean()), float(nz.mean())
    level_sd = float(lv.std(ddof=1)) if len(ref) >= 3 else None
    noise_sd = float(nz.std(ddof=1)) if len(ref) >= 3 else None
    t_level = max(MIN_LEVEL_ADU, 4 * level_sd if level_sd else 0.0)
    t_noise = max(MIN_SPOT_NOISE_ADU, 4 * noise_sd if noise_sd else 0.0, 4 * _noise_error(rows[ref[0]]) * math.sqrt(1 + 1 / len(ref)))

    # Reference master with each frame's offset out (bias steps), at the mean reference level.
    R = _combine(stack[ref] - (lv - ref_level).astype(np.float32)[:, None, None])
    # Noise of one frame's coarse map against R: from the reference frames themselves when there
    # are two or more (they are in R, hence the √(N/(N−1))), never below the read-noise estimate.
    k = len(ref)
    theory = ref_noise / (BIN * CELL) * math.sqrt(1 + 1 / k)
    sm_theory = ref_noise / BIN / (2 * math.sqrt(math.pi) * DIFF_SIGMA) * math.sqrt(1 + 1 / k)
    sigma_cell, sigma_smooth = theory, sm_theory
    if k >= 2:
        f = math.sqrt(k / (k - 1))
        sigma_cell = max(theory, float(np.median([_coarse(stack[i] - R).std() for i in ref])) * f)
        sigma_smooth = max(sm_theory, float(np.median([_robust_std(_smooth(stack[i] - R, DIFF_SIGMA)) for i in ref])) * f)
    floor = 5 * sigma_smooth

    frames: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        D = stack[i] - R
        c = _coarse(D)
        struct = float(c.std())
        ratio = struct / sigma_cell if sigma_cell > 0 else None
        d_level = r["level"] - ref_level
        d_noise = r["noise"] - ref_noise
        noise_sig = d_noise >= t_noise
        struct_sig = bool(ratio and ratio >= SPOT_STRUCT_RATIO and struct >= MIN_SPOT_STRUCT_ADU)
        level_sig = abs(d_level) >= t_level
        if i in ref and not auto:
            verdict = "reference"
        elif noise_sig or struct_sig:
            verdict = "leak"
        elif level_sig:
            verdict = "step"   # level only: a bias step, not light
        else:
            verdict = "tight"
        where = None
        if verdict == "leak" and struct_sig:  # where the light lands, only when the pattern is beyond the noise
            coef, *_ = np.linalg.lstsq(_design(c.shape), c.ravel(), rcond=None)
            h, w = D.shape
            zones = [[_cmean(D[a * h // 3:(a + 1) * h // 3, b * w // 3:(b + 1) * w // 3]) for b in range(3)] for a in range(3)]
            za, zb = divmod(int(np.argmax(np.array(zones))), 3)
            where = {"side": side_name(coef[1], coef[2]), "gradient": round(float(math.hypot(coef[1], coef[2])), 3),
                     "zone": ZONE_NAMES[za][zb], "zones": [[round(v - d_level, 3) for v in row] for row in zones]}
        sd = _smooth(D, DIFF_SIGMA)
        frames.append({
            "index": i, "file": r["file"], "time": r["time"], "ccdTemp": r["ccdTemp"],
            "level": r["level"], "noise": r["noise"], "std": r["std"], "mean": r["mean"], "median": r["median"],
            "levelDelta": round(d_level, 4), "noiseDelta": round(d_noise, 4),
            "structure": round(struct, 4), "structureRatio": round(ratio, 2) if ratio else None,
            "levelSignificant": bool(level_sig), "noiseSignificant": bool(noise_sig), "structureSignificant": struct_sig,
            "verdict": verdict, "where": where, "inReference": i in ref,
            "limit": round(max(float(np.percentile(np.abs(sd), 99.5)), floor), 4),
        })
    probes = [f for f in frames if f["verdict"] != "reference"]
    evaluation = {
        "reference": {"frames": ref, "auto": auto, "level": round(ref_level, 4), "noise": round(ref_noise, 4),
                      "levelScatter": round(level_sd, 4) if level_sd is not None else None,
                      "noiseScatter": round(noise_sd, 5) if noise_sd is not None else None},
        "thresholds": {"level": round(t_level, 4), "noise": round(t_noise, 4), "structureRatio": SPOT_STRUCT_RATIO,
                       "structureCell": round(sigma_cell, 5)},
        "counts": {v: sum(f["verdict"] == v for f in frames) for v in ("leak", "step", "tight", "reference")},
        "sharedLimit": round(max([f["limit"] for f in probes] or [floor]), 4),
        "frames": frames,
    }
    return evaluation, {"R": R}


# ---------------------------------------------------------------------------- images

def _png(rgb: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_master(master: np.ndarray, lo: float | None = None, hi: float | None = None, center: bool = False) -> bytes:
    """Smoothed master, linear between lo and hi; `center` takes its own background out first (lo/hi then relative)."""
    sm = _smooth(master)
    if center:
        sm = sm - float(np.median(sm))
    if lo is None or hi is None or hi <= lo:
        lo, hi = float(np.percentile(sm, 0.5)), float(np.percentile(sm, 99.5))
    x = np.clip((sm - lo) / (hi - lo if hi > lo else 1), 0, 1)
    return _png((x * 255).astype(np.uint8))


DIVERGING = {
    # blue ↔ gray ↔ red (dataviz diverging pair); the midpoint follows the page theme.
    "light": ((0x2A, 0x78, 0xD6), (0xF0, 0xEF, 0xEC), (0xE3, 0x49, 0x48)),
    "dark": ((0x39, 0x87, 0xE5), (0x38, 0x38, 0x35), (0xE6, 0x67, 0x67)),
}


def render_diff(diff: np.ndarray, limit: float, center: bool, theme: str = "light") -> bytes:
    """comparison − baseline on a diverging scale: red = brighter now, blue = darker now."""
    sd = _smooth(diff, DIFF_SIGMA)
    if center:
        sd = sd - float(np.median(sd))
    t = np.clip(sd / max(limit, 1e-6), -1, 1)[..., None]
    neg, mid, pos = (np.array(c, dtype=np.float32) for c in DIVERGING.get(theme, DIVERGING["light"]))
    rgb = np.where(t < 0, mid + (neg - mid) * -t, mid + (pos - mid) * t)
    return _png(rgb.clip(0, 255).astype(np.uint8))
