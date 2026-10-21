"""Framing: star matching, night framings and the comparison with the previous night."""

from __future__ import annotations

import math
import time
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astrodash import framing as fr
from astrodash.api import create_app
from astrodash.config import Config

W, H = 3856, 2180


def _rot(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s], [s, c]])


def _sky(n: int = 400, seed: int = 1) -> np.ndarray:
    """Star positions on a sky larger than one frame, brightest first."""
    rng = np.random.default_rng(seed)
    return rng.uniform([-600, -600], [W + 600, H + 600], size=(n, 2))


def _view(sky: np.ndarray, angle: float = 0.0, shift=(0.0, 0.0), seed: int = 2, w: int = W, h: int = H,
          shuffle: float = 0.0, keep: float = 0.85) -> fr.Stars:
    """A frame looking at `sky`: turned by `angle` about the frame centre and shifted, with centroid
    noise, a few stars lost, a few spurious ones, and (`shuffle`) a different brightness order, as in
    another filter."""
    rng = np.random.default_rng(seed)
    c = np.array([(w - 1) / 2, (h - 1) / 2])
    xy = (sky - c) @ _rot(angle).T + c + np.asarray(shift) + rng.normal(0, 0.2, sky.shape)
    xy = xy[rng.random(len(xy)) < keep]
    xy = np.vstack([xy, rng.uniform([0, 0], [w, h], size=(15, 2))])
    rank = np.arange(len(xy)) + rng.normal(0, shuffle * len(xy), len(xy))
    xy = xy[np.argsort(rank)]
    inside = (xy[:, 0] > 20) & (xy[:, 0] < w - 21) & (xy[:, 1] > 20) & (xy[:, 1] < h - 21)
    return fr.Stars(xy[inside][: fr.MAX_STARS].astype(np.float32), w, h)


@pytest.mark.parametrize("angle,shift", [
    (0.0, (35.0, -12.0)),        # a dither
    (0.4, (-80.0, 25.0)),        # field rotation over a night, re-centred
    (180.0, (10.0, 5.0)),        # meridian flip
    (181.3, (-40.0, 60.0)),      # flip with a slightly turned camera
    (25.0, (150.0, -90.0)),      # camera rotated between nights
    (-90.0, (0.0, 0.0)),
])
def test_register_recovers_the_transform(angle, shift):
    sky = _sky()
    ref = _view(sky, seed=3)
    frm = _view(sky, angle, shift, seed=4, shuffle=0.15)
    fit = fr.register(ref, frm)
    assert fit is not None and fit.matches >= 40
    assert abs(((fit.angle - angle) + 180) % 360 - 180) < 0.02
    assert abs(fit.scale - 1) < 1e-3
    # The reference frame's centre lands where the transform says.
    c = np.array([(W - 1) / 2, (H - 1) / 2])
    assert np.allclose(fit.A @ c + fit.t, c + np.asarray(shift), atol=0.5)


def test_register_refuses_unrelated_fields():
    assert fr.register(_view(_sky(seed=1)), _view(_sky(seed=9))) is None


def test_pose_folds_a_meridian_flip_away():
    sky = _sky()
    ref = _view(sky, seed=3)
    flipped = _view(sky, 180.0, seed=5)
    pose = fr.pose_in_ref(fr.register(ref, flipped), flipped)
    assert abs(pose.rot) < 0.02
    assert fr.corner_shift(fr.Pose((W - 1) / 2, (H - 1) / 2, 0.0), pose, W, H) < 1.0


def test_corner_shift_adds_shift_and_rotation():
    a = fr.Pose(1000.0, 800.0, 0.0)
    assert fr.corner_shift(a, fr.Pose(1030.0, 840.0, 0.0), W, H) == pytest.approx(50.0)
    # A pure turn about the centre moves each corner by 2·sin(θ/2) × the half-diagonal.
    half_diag = math.hypot(W / 2, H / 2)
    assert fr.corner_shift(a, fr.Pose(1000.0, 800.0, 1.0), W, H) == pytest.approx(2 * math.sin(math.radians(0.5)) * half_diag)
    # Rotations either side of the ±90° fold are close, not half a turn apart.
    assert fr.corner_shift(fr.Pose(0, 0, 89.5), fr.Pose(0, 0, -89.5), W, H) == pytest.approx(
        2 * math.sin(math.radians(0.5)) * half_diag)


def test_night_sets_a_differently_framed_frame_aside():
    sky = _sky()
    rng = np.random.default_rng(7)
    stars = {f"f{i}": _view(sky, 0.0, tuple(rng.uniform(-20, 20, 2)), seed=10 + i) for i in range(9)}
    stars["off"] = _view(sky, 0.0, (420.0, -260.0), seed=30)        # a failed centering
    night = fr.night(stars, sorted(stars))
    assert night.outliers == ["off"]
    assert len(night.poses) == 10
    assert 5 < night.spread < 35


def test_compare_judges_shift_and_rotation_against_the_dither():
    sky = _sky()
    rng = np.random.default_rng(11)

    def night_at(angle, shift, seed):
        stars = {f"{seed}-{i}": _view(sky, angle, tuple(np.asarray(shift) + rng.uniform(-15, 15, 2)), seed=seed + i)
                 for i in range(20)}
        return fr.night(stars, sorted(stars)), stars

    prev, prev_stars = night_at(0.0, (0.0, 0.0), 100)
    for (angle, shift), want in [((0.0, (5.0, -3.0)), "ok"), ((0.0, (60.0, 0.0)), "bad"), ((2.0, (0.0, 0.0)), "bad")]:
        cur, cur_stars = night_at(angle, shift, 200 + int(angle * 10) + int(shift[0]))
        res = fr.compare(prev, cur, prev_stars[prev.anchor], cur_stars[cur.anchor])
        assert res["level"] == want, (angle, shift, res)
        assert res["rotation"] == pytest.approx(-angle, abs=0.05)
        assert set(res["byFrame"]) == set(cur.poses)


# ---------------------------------------------------------------------------- pixels and the API

def _render(path: Path, sky: np.ndarray, flux: np.ndarray, angle: float, shift, w: int = 900, h: int = 600, **header) -> None:
    from astropy.io import fits

    rng = np.random.default_rng(zlib.crc32(path.name.encode()))
    img = 300.0 + rng.normal(0, 4.0, (h, w))
    c = np.array([(w - 1) / 2, (h - 1) / 2])
    xy = (sky - c) @ _rot(angle).T + c + np.asarray(shift)
    yy, xx = np.mgrid[0:13, 0:13]
    for (x, y), f in zip(xy, flux):
        x0, y0 = int(round(x)) - 6, int(round(y)) - 6
        if x0 < 0 or y0 < 0 or x0 + 13 > w or y0 + 13 > h:
            continue
        img[y0:y0 + 13, x0:x0 + 13] += f / (2 * math.pi * 4.0) * np.exp(-((xx + x0 - x) ** 2 + (yy + y0 - y) ** 2) / 8.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.clip(img, 0, 65535).astype(np.uint16))
    for k, v in header.items():
        hdu.header[k.replace("_", "-")] = v
    hdu.writeto(path)


def _small_sky(seed: int = 1, w: int = 900, h: int = 600) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    return rng.uniform([-150, -150], [w + 150, h + 150], size=(260, 2)), rng.uniform(800, 9000, 260)


def test_detect_finds_stars_in_sensor_pixels(tmp_path):
    sky, flux = _small_sky()
    _render(tmp_path / "a.fits", sky, flux, 0.0, (0.0, 0.0), IMAGETYP="LIGHT", XBINNING=1)
    _render(tmp_path / "b.fits", sky, flux, 3.0, (25.0, -10.0), IMAGETYP="LIGHT", XBINNING=1)
    a, b = fr.detect(tmp_path / "a.fits"), fr.detect(tmp_path / "b.fits")
    assert (a.w, a.h) == (900, 600) and len(a.xy) >= 60
    # Every detection sits on a rendered star.
    d = np.min(np.hypot(*(a.xy[:, None, :] - sky[None, :, :]).transpose(2, 0, 1)), axis=1)
    assert np.median(d) < 0.3
    fit = fr.register(a, b)
    assert fit.angle == pytest.approx(3.0, abs=0.02)


def _light(night: str, i: int, filt: str = "H") -> dict:
    return dict(IMAGETYP="LIGHT", FILTER=filt, EXPOSURE=300.0, XBINNING=1, READOUTM="Linearity HDR", GAIN=9, OFFSET=100,
                SET_TEMP=-10.0, INSTRUME="Cam", TELESCOP="Scope", FOCALLEN=250.0,
                DATE_OBS=f"{night}T03:{10 + i:02d}:00", DATE_LOC=f"{night}T22:{10 + i:02d}:00")


def test_framing_api_compares_each_night_with_the_one_before(tmp_path):
    root = tmp_path / "Astro"
    nights = root / "NGC 1 (Test)" / ".SessionData"
    sky, flux = _small_sky()
    plan = {"2026-09-01": (0.0, (0, 0)), "2026-09-02": (0.0, (4, -3)), "2026-09-03": (6.0, (0, 0))}
    dithers = [(0, 0), (5, -4), (-4, 3)]
    for night, (angle, shift) in plan.items():
        for i, d in enumerate(dithers):
            filt = "H" if i < 2 else "O"
            _render(nights / f"NIGHT_{night}" / "LIGHT" / filt / f"{night}_{i}.fits", sky, flux, angle,
                    (shift[0] + d[0], shift[1] + d[1]), **_light(night, i, filt))
    cfg = Config(data_root=root, db_path=tmp_path / "var" / "framing.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        assert client.get("/api/framing/revisions/nope").status_code == 404
        for _ in range(600):
            body = client.get("/api/framing/revisions/ngc-1-test").json()
            if body["job"] is None and all(v[""]["status"] != "pending" for v in body["sessions"].values()):
                break
            time.sleep(0.1)
        s = body["sessions"]
        assert s["NIGHT_2026-09-01"][""]["status"] == "first"
        assert s["NIGHT_2026-09-02"][""]["level"] == "ok"
        assert s["NIGHT_2026-09-02"][""]["previous"] == "NIGHT_2026-09-01"
        third = s["NIGHT_2026-09-03"][""]
        assert third["level"] == "bad" and third["rotation"] == pytest.approx(-6.0, abs=0.05)
        assert "byFrame" not in third

        one = client.get("/api/framing/sessions/ngc-1-test/NIGHT_2026-09-03").json()
        assert one["job"] is None
        (panel,) = one["panels"]
        assert panel["panel"] is None and panel["verdict"]["level"] == "bad"
        assert set(panel["frames"]) == {f"NGC 1 (Test)/.SessionData/NIGHT_2026-09-03/LIGHT/{f}/2026-09-03_{i}.fits"
                                        for i, f in enumerate(["H", "H", "O"])}
        assert {x["level"] for x in panel["frames"].values()} == {"bad"}
        assert client.get("/api/framing/sessions/ngc-1-test/NIGHT_1999-01-01").status_code == 404

        # Live: a light of the next night, back on the first night's framing (it lands next to the target folder).
        live = root / ".Download" / "2026-09-04" / "NGC 1" / "LIGHT" / "H" / "x.fits"
        _render(live, sky, flux, 0.0, (2, 1), OBJECT="NGC 1", **_light("2026-09-04", 0))
        svc = app.state.engine.framing
        for _ in range(100):
            res = svc.live(str(live), "ngc-1-test", None, "2026-09-04")
            if res["status"] != "pending":
                break
            time.sleep(0.1)
        assert res["status"] == "ready" and res["previous"] == "NIGHT_2026-09-03" and res["level"] == "bad"
        assert svc.live(str(live), None, None, "2026-09-04")["status"] == "no-target"
        assert svc.live(str(live), "ngc-1-test", None, "2026-09-01")["status"] == "first"
