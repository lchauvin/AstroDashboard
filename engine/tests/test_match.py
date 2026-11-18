"""Match: quality per hour measured from synthetic subs (vignetting, gradient, nebula, noise)."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astrodash import match
from astrodash.api import create_app
from astrodash.config import Config

H, W = 1090, 1928   # half the camera, to keep the test quick
PEDESTAL = 400.0


def _vignetting() -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W]
    r2 = ((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2
    return 1.0 - 0.08 * r2


def _nebula(amp: float) -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W]
    return amp * np.exp(-(((x - W * 0.55) / 180) ** 2 + ((y - H * 0.45) / 140) ** 2))


def _light(amp: float, sky: float = 600.0, noise: float = 20.0, seed: int = 0, flip: bool = False) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:H, 0:W]
    gradient = 30.0 * (x / W) + 10.0 * (y / H)
    sky_img = (sky + gradient + _nebula(amp)) * _vignetting()
    if flip:  # after a meridian flip the sky turns 180° on the sensor, the vignetting does not
        sky_img = (np.rot90(sky + gradient + _nebula(amp), 2)) * _vignetting()
    return PEDESTAL + sky_img + rng.normal(0, noise, (H, W))


def _flat() -> np.ndarray:
    rng = np.random.default_rng(99)
    f = 30000 * _vignetting() + rng.normal(0, 50, (H, W))
    b = match.bin_image(f)
    return b / np.median(b)


def _q(amp: float, noise: float = 20.0, seeds=range(8), flip=False, mask=None):
    flat = _flat()
    imgs = [match.calibrated(match.bin_image(_light(amp, noise=noise, seed=s, flip=flip)), flat, PEDESTAL, flip) for s in seeds]
    if mask is None:  # these frames are the mask filter's: each measured on the other half's mask
        mask, own = match.cross_masks(imgs)
    else:             # another filter's frames: the target's mask
        own = [mask] * len(imgs)
    qs = [match.quality_per_hour(match.contrast(i, m), noise, 300.0) for i, m in zip(imgs, own)]
    return float(np.mean(qs)), mask


def test_quality_scales_with_brightness_squared_and_noise():
    q_bright, mask = _q(40.0)
    q_faint, _ = _q(10.0, mask=mask)
    assert q_bright / q_faint == pytest.approx(16, rel=0.15)
    q_noisy, _ = _q(40.0, noise=40.0, mask=mask)
    assert q_bright / q_noisy == pytest.approx(4, rel=0.15)


def test_flat_and_plane_remove_vignetting_and_gradient():
    # No nebula: what is left after the flat and the plane is noise, not contrast.
    q_empty, _ = _q(0.0)
    q_faint, _ = _q(10.0)
    assert q_empty < 0.1 * q_faint


def test_meridian_flip_is_turned_back():
    q, mask = _q(40.0)
    q_flipped, _ = _q(40.0, flip=True, mask=mask)
    assert q_flipped == pytest.approx(q, rel=0.15)


def test_mask_is_bounded():
    flat_noise = np.random.default_rng(1).normal(0, 1, (72, 72)).astype(np.float32)
    assert match.MASK_MIN <= match.target_mask(flat_noise)[0].mean() <= match.MASK_MAX + 0.01
    everywhere = np.ones((72, 72), np.float32) * 100
    everywhere[:, :36] += 50
    assert match.target_mask(everywhere)[0].mean() <= match.MASK_MAX + 0.01


def test_frame_round_trip(tmp_path):
    from astropy.io import fits

    path = tmp_path / "l.fits"
    fits.PrimaryHDU(_light(40.0).astype(np.float32)).writeto(path)
    p, data, err = match.measure_frame(str(path))
    assert err is None and match.from_bytes(data).shape == (H * 4 // 5 // match.BIN,) * 2
    key, flat, err = match.measure_flat("k", [str(path)], PEDESTAL)
    assert err is None and np.median(match.from_bytes(flat)) == pytest.approx(1.0)
    assert match.measure_frame(str(tmp_path / "missing.fits"))[2]


def test_api_without_analysed_nights(data_root, tmp_path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "m.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        assert client.get("/api/match/revisions/nope").status_code == 404
        out = client.get("/api/match/revisions/ic-1848-soul-nebula").json()
        assert out["filters"] == {} and out["analysedNights"] == 0 and not out["pending"]
        assert "ic-1848-soul-nebula" in client.get("/api/match/summary").json()["revisions"]
