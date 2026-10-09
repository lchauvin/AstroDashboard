"""Stretched JPEG previews of single frames, cached in var/previews.

Frames are opened read-only. The stretch is a PixInsight-style auto STF
(median / MAD midtones transfer), which makes gradients, obstructions and
trails visible at a glance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

PREVIEW_WIDTH = 1400


def _load(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".xisf":
        from xisf import XISF

        data = XISF(str(path)).read_image(0)
        if data.ndim == 3:
            data = data[..., 0] if data.shape[-1] <= 4 else data[0]
    else:
        from astropy.io import fits

        with fits.open(path, memmap=False) as hdul:
            data = next(h.data for h in hdul if h.data is not None)
        if data.ndim == 3:
            data = data[0]
    return np.asarray(data, dtype=np.float32)


def _mtf(m: float, x: np.ndarray) -> np.ndarray:
    return ((m - 1) * x) / ((2 * m - 1) * x - m)


def auto_stretch(img: np.ndarray) -> np.ndarray:
    """PixInsight STF defaults: shadows clip at median − 2.8·MAD, target background 0.25."""
    finite = img[np.isfinite(img)]
    lo, hi = float(finite.min()), float(finite.max())
    x = (img - lo) / (hi - lo if hi > lo else 1.0)
    x = np.nan_to_num(np.clip(x, 0, 1))
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    c0 = min(max(med - 2.8 * mad, 0.0), 1.0)
    x = np.clip((x - c0) / (1 - c0 if c0 < 1 else 1), 0, 1)
    m_bg = (med - c0) / (1 - c0) if c0 < 1 else 0.5
    target = 0.25
    m = _mtf(target, np.array(m_bg)).item() if 0 < m_bg < 1 else 0.5
    return _mtf(m, x)


def preview(path: Path, out_dir: Path) -> Path:
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:20]
    out = out_dir / f"{key}.jpg"
    if out.is_file():
        return out
    from PIL import Image

    img = _load(path)
    step = max(1, img.shape[1] // PREVIEW_WIDTH)
    img = img[::step, ::step]
    rgb = (auto_stretch(img) * 255).astype(np.uint8)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    Image.fromarray(rgb).save(tmp, "JPEG", quality=88)
    tmp.replace(out)  # inside var/previews only
    return out
