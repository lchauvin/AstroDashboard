"""Do the flats correct the lights they will be applied to? (PLAN.md §4.2, experimental)

Median-stack a handful of kept lights and the flats of the chosen set (both
downsampled and normalised), divide, and look at what is left:

* **Dust** — the flat's small-scale dips (dust donuts, ≥ 1.5 % deep) are found
  with a high-pass filter, and we measure what *fraction* of that depth shows up
  in the stacked lights at the same pixels. ~1 means the same dust is there and
  the flat will remove it; ~0 means the dust is gone (moved, filter or camera
  rotated, flats from another night) and the flat will *add* bright rings. When
  the lights are too noisy to tell, the check says so instead of guessing.
* **Vignetting** — the corrected background should be flat from centre to corner;
  a residual radial slope means the flat over- or under-corrects the light fall-off.

Read-only: frames are only opened.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

BIN = 4                 # 3856×2180 → 964×545
HP_SIGMA = 12.0         # high-pass scale in binned pixels (~50 px full resolution)
DUST_DIP = 0.015        # a flat pixel ≥ 1.5 % below its surroundings is dust
MIN_DUST_PIXELS = 60    # binned pixels (~1000 full-resolution pixels)
DUST_MOVED = 0.4        # < 40 % of the flat's dust depth found in the lights
MAX_DUST_ERROR = 0.15   # the depth fraction must be known to ± 15 % to judge
MAX_FRAMES = 10


def _load(path: str) -> np.ndarray:
    from ..preview import _load as load

    img = load(Path(path))
    h, w = (img.shape[0] // BIN) * BIN, (img.shape[1] // BIN) * BIN
    img = img[:h, :w].reshape(h // BIN, BIN, w // BIN, BIN).mean(axis=(1, 3))
    med = float(np.median(img))
    return img / med if med > 0 else img


def _stack(paths: list[str]) -> np.ndarray:
    return np.median(np.stack([_load(p) for p in paths[:MAX_FRAMES]]), axis=0)


def _highpass(img: np.ndarray) -> np.ndarray:
    from scipy.ndimage import gaussian_filter

    smooth = gaussian_filter(img, HP_SIGMA, mode="nearest")
    return img / np.where(smooth > 0, smooth, 1) - 1


def _background(img: np.ndarray, block: int = 16) -> np.ndarray:
    """Coarse sky map: the 20th percentile of each block (ignores stars and most nebula)."""
    h, w = (img.shape[0] // block) * block, (img.shape[1] // block) * block
    blocks = img[:h, :w].reshape(h // block, block, w // block, block).swapaxes(1, 2).reshape(h // block, w // block, -1)
    return np.percentile(blocks, 20, axis=2)


def _radial_slope(bg: np.ndarray) -> float:
    """Relative change of the background from centre (r=0) to corner (r=1), by least squares."""
    yy, xx = np.mgrid[: bg.shape[0], : bg.shape[1]]
    cy, cx = (bg.shape[0] - 1) / 2, (bg.shape[1] - 1) / 2
    r = np.hypot((yy - cy) / cy, (xx - cx) / cx) / math.sqrt(2)
    a, b = np.polyfit(r.ravel(), bg.ravel(), 1)
    return float(a / b) if b else 0.0


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < MIN_DUST_PIXELS or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def compare(light_paths: list[str], flat_paths: list[str]) -> dict[str, Any]:
    light = _stack(light_paths)
    flat = _stack(flat_paths)
    if light.shape != flat.shape:
        return {"verdict": "bad", "notes": [f"Flats are {flat.shape[1] * BIN}×{flat.shape[0] * BIN} and lights "
                                            f"{light.shape[1] * BIN}×{light.shape[0] * BIN} — they cannot be divided."]}
    corrected = light / np.where(flat > 0, flat, 1)

    flat_hp = _highpass(flat)
    light_hp = _highpass(light)
    corr_hp = _highpass(corrected)
    # The high-pass is unreliable within ~2σ of the edges (the blur runs out of image there).
    edge = int(2 * HP_SIGMA)
    inner = np.zeros(flat.shape, dtype=bool)
    inner[edge:-edge, edge:-edge] = True
    dust = (flat_hp < -DUST_DIP) & inner
    stars = light_hp > 0.05                      # keep stars out of the comparison
    sel = dust & ~stars
    n_dust = int(sel.sum())
    clean = ~(flat_hp < -DUST_DIP / 3) & ~stars & inner
    noise = 1.4826 * float(np.median(np.abs(light_hp[clean] - np.median(light_hp[clean])))) if clean.any() else 1.0
    depth = float(flat_hp[sel].mean()) if n_dust else 0.0
    # Fraction of the flat's dust depth present in the lights, and its uncertainty.
    # Neighbouring binned pixels are correlated (≈ HP smoothing), so count independent samples conservatively.
    fraction = float(light_hp[sel].mean()) / depth if n_dust and depth else None
    fraction_err = (noise / math.sqrt(max(n_dust / 4, 1))) / abs(depth) if n_dust and depth else None
    after = _corr(flat_hp[sel], corr_hp[sel])

    raw_slope = _radial_slope(_background(light))
    corr_slope = _radial_slope(_background(corrected))

    notes: list[str] = []
    verdict = "ok"
    if n_dust < MIN_DUST_PIXELS:
        notes.append("The flats show no significant dust shadows (none ≥ 1.5 % deep), so only vignetting was checked.")
    elif fraction is None or fraction_err is None or fraction_err > MAX_DUST_ERROR:
        notes.append(f"The flats have dust shadows ({abs(depth):.1%} deep) but the stacked lights are too noisy to tell "
                     "whether the same dust is in them.")
    elif fraction < DUST_MOVED:
        verdict = "warn"
        notes.append(f"Only {max(fraction, 0):.0%} of the flats' dust shadows ({abs(depth):.1%} deep) appear in the lights: "
                     "the dust moved, or the camera or filter rotated, since the flats were taken — flat-fielding will add "
                     "bright rings where the dust used to be.")
    else:
        notes.append(f"Dust shadows match: {fraction:.0%} of the flats' dust depth ({abs(depth):.1%}) is in the lights.")
    if abs(corr_slope) > 0.05 and abs(corr_slope) > 0.5 * abs(raw_slope):
        verdict = "warn"
        side = "brighter" if corr_slope > 0 else "darker"
        notes.append(f"Corners stay {abs(corr_slope):.0%} {side} than the centre after flat-fielding "
                     f"(raw lights {raw_slope:+.0%}): the vignetting is not fully corrected — check that flats and lights "
                     "share the same optical train and spacing.")
    else:
        notes.append(f"Vignetting corrected: corners within {abs(corr_slope):.0%} of the centre (raw {raw_slope:+.0%}).")

    return {
        "verdict": verdict,
        "dustPixels": n_dust,
        "dustDepth": round(depth, 4),
        "dustInLights": None if fraction is None else round(fraction, 3),
        "dustInLightsError": None if fraction_err is None else round(fraction_err, 3),
        "dustAfterCorrection": None if after is None else round(after, 3),
        "vignettingRaw": round(raw_slope, 4),
        "vignettingCorrected": round(corr_slope, 4),
        "notes": notes,
    }
