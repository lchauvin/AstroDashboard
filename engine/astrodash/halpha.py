"""The absolute Hα anchor: a target's surface brightness in Rayleighs from the Finkbeiner composite.

Port of AstroPlanner's `src/server/halpha.ts` (measurement half only): the
web app decides between this survey value and a per-class typical value,
because the class mapping lives with the vendored SNR model.

The survey has a ~1° beam (its WHAM component), so anything smaller than that
is beam-diluted and the measurement is reported but flagged. The background
annulus is pushed outside the beam, otherwise it is full of the same smeared
object flux and subtracting it removes the signal too.

The cutout comes from the CDS hips2fits service (a public read-only GET) and the
result is cached in the dashboard database forever: the sky does not change.
"""

from __future__ import annotations

import io
import math
import urllib.parse
import urllib.request
from typing import Any

import numpy as np

HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
SURVEY = "CDS/P/Finkbeiner"
USER_AGENT = "AstroDashboard/0.1 (local astrophotography planning tool)"
SURVEY_BEAM_ARCMIN = 60.0


def _fetch_cutout(ra: float, dec: float, fov_deg: float, size: int) -> np.ndarray:
    from astropy.io import fits

    params = urllib.parse.urlencode({
        "hips": SURVEY, "ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "fov": f"{fov_deg:.5f}",
        "width": size, "height": size, "projection": "TAN", "coordsys": "icrs", "format": "fits",
    })
    req = urllib.request.Request(f"{HIPS2FITS}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 — fixed https host
        payload = res.read()
    with fits.open(io.BytesIO(payload)) as hdul:
        return np.asarray(next(h.data for h in hdul if h.data is not None), dtype=np.float64)


def measure(ra: float, dec: float, maj_arcmin: float | None, min_arcmin: float | None, pa_deg: float | None,
            fetch=_fetch_cutout) -> dict[str, Any]:
    maj = float(maj_arcmin or 10.0)
    mnr = float(min_arcmin or maj)
    radius = maj / 2
    annulus_inner = max(radius * 2, SURVEY_BEAM_ARCMIN * 1.5)
    annulus_scale = annulus_inner / radius
    needed = annulus_inner * 1.6 * 2 * 1.15
    fov = min(12.0, max(0.6, needed / 60))
    size = int(min(512, max(64, round(fov * 60))))

    img = fetch(ra, dec, fov, size)
    h, w = img.shape
    px_per_arcmin = size / (fov * 60)
    yy, xx = np.mgrid[0:h, 0:w]
    dx, dy = xx - (w - 1) / 2, yy - (h - 1) / 2
    t = math.radians(pa_deg or 0)
    a = max(1.0, maj * px_per_arcmin / 2)
    b = max(1.0, mnr * px_per_arcmin / 2)
    u = (dx * math.cos(t) + dy * math.sin(t)) / a
    v = (-dx * math.sin(t) + dy * math.cos(t)) / b
    r = np.hypot(u, v)
    finite = np.isfinite(img)
    inside = img[(r <= 1) & finite]
    ring = img[(r > annulus_scale) & (r <= annulus_scale * 1.6) & finite]

    object_r = float(inside.mean()) if inside.size else float("nan")
    background_r = float(np.median(ring)) if ring.size else 0.0
    net_r = max(0.0, object_r - background_r) if math.isfinite(object_r) else 0.0
    return {
        "survey": SURVEY,
        "objectR": round(object_r, 2),
        "backgroundR": round(background_r, 2),
        "netR": round(net_r, 2),
        "peakR": round(float(inside.max()), 2) if inside.size else None,
        "objectArcmin": maj,
        "beamDiluted": maj < SURVEY_BEAM_ARCMIN,
    }
