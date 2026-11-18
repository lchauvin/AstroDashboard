"""Match: how much signal a target gathers per hour in each filter, measured from its own subs.

A goal can say "as good as <reference target> in this filter". The hours that takes are the
reference's quality divided by this target's quality per hour, both measured the same way:

* each kept light (a sample per night and filter) is binned BIN×BIN over a central square
  (median, so stars vanish), the bias pedestal is taken off and it is divided by the night's
  flat for that filter, binned the same way (vignetting would otherwise read as nebula);
* a plane fitted to the darkest pixels is subtracted (the light-pollution gradient, what a
  background extraction removes before you judge an image);
* one mask per target (and mosaic panel): the bright part of the median stack of the filter
  that shows the target most clearly, so faint OIII is measured where Hα shows the nebula;
* contrast C = mean(mask) − mean(darkest half outside it), in ADU; noise σ = the frame's
  per-pixel background noise (astro-eval's noise_mad); quality q = (C/σ)², which adds up over
  subs like SNR² and does not depend on the gain or the sub length per hour.

Frames after a meridian flip (PIERSIDE West) are turned 180° after the flat (the flat follows
the camera, the nebula follows the sky). Read-only: frames are only opened.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

BIN = 24                 # 3856×2180, 80 % square crop → 72×72
CROP = 0.8               # side of the central square, as a fraction of the short side
PLANE_FRACTION = 0.3     # the plane is fitted to the darkest 30 % (three passes)
MASK_SIGMA = 3.0         # mask: above the dark floor by this many binned noise σ …
MASK_MIN, MASK_MAX = 0.02, 0.4   # … but at least 2 % and at most 40 % of the square


def bin_image(img: np.ndarray, b: int = BIN, crop: float = CROP) -> np.ndarray:
    h, w = img.shape
    s = int(min(h, w) * crop) // b * b
    if s < b * 8:
        raise ValueError(f"image too small ({w}×{h})")
    y0, x0 = (h - s) // 2, (w - s) // 2
    a = img[y0:y0 + s, x0:x0 + s].reshape(s // b, b, s // b, b).transpose(0, 2, 1, 3).reshape(s // b, s // b, b * b)
    return np.median(a, axis=2).astype(np.float32)


def _load(path: str) -> np.ndarray:
    from .preview import _load as load

    return load(Path(path))


def measure_frame(path: str) -> tuple[str, bytes | None, str | None]:
    """A light, binned (raw ADU). Runs in a worker process."""
    try:
        return path, bin_image(_load(path)).tobytes(), None
    except Exception as exc:  # noqa: BLE001 — an unreadable frame is recorded, not retried
        return path, None, f"{type(exc).__name__}: {exc}"


def measure_flat(key: str, paths: list[str], pedestal: float) -> tuple[str, bytes | None, str | None]:
    """A night's flats for one filter: median of the binned frames, pedestal off, normalised to 1."""
    try:
        stack = np.median([bin_image(_load(p)) - pedestal for p in paths], axis=0)
        med = float(np.median(stack))
        if med <= 0:
            raise ValueError("flat level at or below the pedestal")
        return key, (stack / med).astype(np.float32).tobytes(), None
    except Exception as exc:  # noqa: BLE001
        return key, None, f"{type(exc).__name__}: {exc}"


def flat_key(paths: list[str], pedestal: float) -> str:
    h = hashlib.sha1(("|".join(sorted(paths)) + f"|{pedestal}").encode()).hexdigest()[:16]
    return f"flat:{h}"


def from_bytes(data: bytes) -> np.ndarray:
    a = np.frombuffer(data, dtype=np.float32)
    n = int(round(len(a) ** 0.5))
    return a.reshape(n, n)


def remove_plane(img: np.ndarray) -> np.ndarray:
    n = img.shape[0]
    yy, xx = (np.mgrid[0:n, 0:n] + 0.5) / n - 0.5
    sel = img <= np.percentile(img, PLANE_FRACTION * 100)
    model = np.zeros_like(img)
    for _ in range(3):
        a = np.c_[np.ones(int(sel.sum())), xx[sel], yy[sel]]
        coef, *_ = np.linalg.lstsq(a, img[sel], rcond=None)
        model = coef[0] + coef[1] * xx + coef[2] * yy
        resid = img - model
        sel = resid <= np.percentile(resid, PLANE_FRACTION * 100)
    return (img - model).astype(np.float32)


def calibrated(raw: np.ndarray, flat: np.ndarray, pedestal: float, flipped: bool) -> np.ndarray:
    img = remove_plane((raw - pedestal) / np.maximum(flat, 0.05))
    return np.rot90(img, 2) if flipped else img


def _pixel_noise(img: np.ndarray) -> float:
    """Noise of a binned image, from neighbour differences (insensitive to smooth structure)."""
    d = np.diff(img, axis=1).ravel()
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2))


Mask = tuple[np.ndarray, np.ndarray]


def target_mask(stack: np.ndarray) -> Mask:
    """(nebula, background): the stack's bright part and the darker half of the rest.

    Both regions come from the stack, never from the frame being measured, so a frame's own
    noise does not pick its brightest or darkest pixels (that would read as contrast).
    """
    floor = np.percentile(stack, 5)
    thr = floor + MASK_SIGMA * _pixel_noise(stack)
    frac = float((stack > thr).mean())
    if frac > MASK_MAX:
        thr = np.percentile(stack, 100 * (1 - MASK_MAX))
    elif frac < MASK_MIN:
        thr = np.percentile(stack, 100 * (1 - MASK_MIN))
    nebula = stack > thr
    rest = stack[~nebula]
    background = ~nebula & (stack <= np.median(rest))
    return nebula, background


def structure(stack: np.ndarray) -> float:
    """How clearly a stack shows the target: its bright end over its pixel noise."""
    noise = _pixel_noise(stack)
    return float((np.percentile(stack, 95) - np.percentile(stack, 50)) / noise) if noise > 0 else 0.0


def mask_filter(frames: list[dict[str, Any]]) -> str:
    """The filter whose stack shows the target most clearly (usually Hα, or L for a galaxy)."""
    by: dict[str, list[np.ndarray]] = {}
    for f in frames:
        by.setdefault(f["filter"], []).append(f["img"])
    return max(by, key=lambda k: structure(np.median(by[k], axis=0)) if len(by[k]) >= 2 else 0.0)


def cross_masks(imgs: list[np.ndarray]) -> tuple[Mask, list[Mask]]:
    """The target's mask from all these frames, and one per frame from the other half only.

    The frames a mask is built from are measured with the mask of the other half (even/odd),
    so no frame is measured on regions its own noise helped choose.
    """
    full = target_mask(np.median(imgs, axis=0))
    if len(imgs) < 2:
        return full, [full] * len(imgs)
    even = target_mask(np.median(imgs[0::2], axis=0))
    odd = target_mask(np.median(imgs[1::2], axis=0))
    return full, [odd if i % 2 == 0 else even for i in range(len(imgs))]


def contrast(img: np.ndarray, mask: Mask) -> float:
    nebula, background = mask
    return float(img[nebula].mean() - img[background].mean())


def quality_per_hour(c: float, noise: float, exptime: float) -> float:
    """(C/σ)² of one sub, scaled to an hour of such subs."""
    if noise <= 0 or exptime <= 0:
        return 0.0
    return max(0.0, c) ** 2 / noise ** 2 * 3600.0 / exptime


def summarize(frames: list[dict[str, Any]], mask_filter: str) -> dict[str, dict[str, Any]]:
    """frames: {filter, night, img (calibrated, oriented), noise, exptime} → per filter.

    The mask comes from `mask_filter`'s frames (cross masks for those, the full one for the rest).
    """
    by: dict[str, list[dict[str, Any]]] = {}
    for f in frames:
        by.setdefault(f["filter"], []).append(f)
    full, own = cross_masks([f["img"] for f in by[mask_filter]])
    out: dict[str, dict[str, Any]] = {}
    for filt, fs in by.items():
        masks = own if filt == mask_filter else [full] * len(fs)
        qs = [quality_per_hour(contrast(f["img"], m), f["noise"], f["exptime"]) for f, m in zip(fs, masks)]
        out[filt] = {
            "qPerHour": round(float(np.mean(qs)), 5),
            "spread": round(float(np.std(qs) / np.mean(qs)), 3) if np.mean(qs) > 0 and len(qs) > 1 else None,
            "frames": len(fs),
            "nights": len({f["night"] for f in fs}),
        }
    return out
