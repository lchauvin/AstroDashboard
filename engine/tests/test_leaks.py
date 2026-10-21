"""Light-leak finder: synthetic dark sets, with and without a glow from one side."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from astropy.io import fits
from fastapi.testclient import TestClient

from astrodash import leaks
from astrodash.api import create_app
from astrodash.config import Config

H, W = 256, 384


def write_darks(folder: Path, n: int, seed: int, glow: float = 0.0, exposure: float = 60.0, imagetyp: str = "DARK",
                start_min: int = 0, step_every: int = 0, light: float = 0.0, filt: str | None = None) -> None:
    """Integer-ADU darks: 400 ADU bias, 1.4 ADU read noise, a few hot pixels, optional glow from the left."""
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    hot = np.random.default_rng(99).integers(0, H * W, 40)
    x = np.linspace(1, 0, W) ** 2
    for i in range(n):
        img = 400 + rng.normal(0, 1.4, (H, W)) + glow * x[None, :]
        if step_every and i % step_every == 0:
            img += 1.0  # the bias level steps up on some frames
        if light:
            img += rng.poisson(light * 0.7, (H, W)) / 0.7  # photons, with their shot noise (0.7 e-/ADU)
        img.flat[hot] += 300
        hdr = fits.Header()
        hdr.update(IMAGETYP=imagetyp, EXPOSURE=exposure, GAIN=9, OFFSET=100, READOUTM="Linearity HDR",
                   XBINNING=1, **{"SET-TEMP": -10.0, "CCD-TEMP": -10.0}, INSTRUME="Cam",
                   **{"DATE-OBS": f"2026-10-01T03:{start_min + i:02d}:00"}, **({"FILTER": filt} if filt else {}))
        fits.PrimaryHDU(np.round(img).astype(np.int16), hdr).writeto(folder / f"d{exposure:g}_{start_min + i:03d}.fits")


def test_find_groups_by_folder_and_setting(tmp_path):
    write_darks(tmp_path / "a", 3, 1)
    write_darks(tmp_path / "a", 2, 2, exposure=120.0, start_min=10)
    write_darks(tmp_path / "b", 2, 3, imagetyp="FLAT")
    write_darks(tmp_path / "c", 2, 4, imagetyp="LIGHT")
    cfg = Config(data_root=tmp_path)
    found = leaks.find_groups(tmp_path, cfg.gain_ignored)
    rows = [(Path(g["folder"]).name, g["kind"], g["exposure"], g["frames"], g["gain"]) for g in found["groups"]]
    # Flats never; capped lights (phase 1) yes; HDR ignores gain/offset.
    assert rows == [("a", "dark", 60.0, 3, None), ("a", "dark", 120.0, 2, None), ("c", "light", 60.0, 2, None)]


def test_compare_flags_glow_and_not_noise(tmp_path):
    cfg = Config(data_root=tmp_path)
    write_darks(tmp_path / "base", 8, 1)
    write_darks(tmp_path / "same", 8, 2)
    write_darks(tmp_path / "leak", 8, 3, glow=3.0)
    want = {"exposure": 60.0, "setTemp": -10.0, "readout": "Linearity HDR", "gain": None, "offset": None, "binning": 1}
    out = {k: leaks.analyse_set(leaks.set_files(tmp_path / k, want, cfg.gain_ignored)) for k in ("base", "same", "leak")}
    assert out["base"][0]["frames"] == 8

    same = leaks.compare(out["base"][0], out["same"][0], out["base"][1], out["same"][1])
    assert same["verdict"] == "same" and not same["findings"]

    worse = leaks.compare(out["base"][0], out["leak"][0], out["base"][1], out["leak"][1])
    assert worse["verdict"] == "worse"
    assert worse["diff"]["gradient"]["side"] == "left" and worse["diff"]["gradient"]["significant"]
    assert worse["diff"]["edgeSignificant"]["left"] and worse["diff"]["edges"]["left"] > 0.3
    # Covering the leak again reads as an improvement.
    better = leaks.compare(out["leak"][0], out["base"][0], out["leak"][1], out["base"][1])
    assert better["verdict"] == "better"

    png = leaks.render_diff(out["leak"][1]["master"] - out["base"][1]["master"], worse["display"]["structureLimit"], True)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_bias_steps_between_frames_are_not_a_leak(tmp_path):
    cfg = Config(data_root=tmp_path)
    write_darks(tmp_path / "base", 8, 1)
    write_darks(tmp_path / "steps", 8, 2, step_every=3)  # frames 0, 3, 6 sit 1 ADU higher
    want = {"exposure": 60.0, "setTemp": -10.0, "readout": "Linearity HDR", "gain": None, "offset": None, "binning": 1}
    out = {k: leaks.analyse_set(leaks.set_files(tmp_path / k, want, cfg.gain_ignored)) for k in ("base", "steps")}
    c = leaks.compare(out["base"][0], out["steps"][0], out["base"][1], out["steps"][1])
    assert not c["diff"]["gradient"]["significant"] and not any(c["diff"]["edgeSignificant"].values())
    assert any("scatter" in n["text"] for n in c["notes"])
    # The masters sit at the mean frame level, like the level comparison.
    assert abs(c["diff"]["level"] - c["level"]["delta"]) < 0.02


def _wait(client, test_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        t = client.get(f"/api/leaks/{test_id}").json()
        if all(s["status"] in ("done", "error") for s in t["sets"] + t["checks"]):
            return t
        time.sleep(0.1)
    raise AssertionError("analysis did not finish")


def test_api_chain_of_sets(tmp_path):
    root = tmp_path / "Astro"
    write_darks(root / "leaktest" / "1", 6, 1, glow=3.0)
    write_darks(root / "leaktest" / "2", 6, 2)
    before = sorted((p.name, p.stat().st_mtime) for p in root.rglob("*.fits"))
    cfg = Config(data_root=root, db_path=tmp_path / "var" / "db.sqlite")
    app = create_app(cfg, scan_on_start=False, observatory=False)
    with TestClient(app) as client:
        groups = client.get("/api/leaks/browse", params={"folder": "leaktest"}).json()["groups"]
        assert [g["frames"] for g in groups] == [6, 6]
        t = client.post("/api/leaks", json={"name": "RedCat"}).json()
        for g, note in zip(groups, ["Baseline", "Foil over the camera joint"]):
            body = {k: g[k] for k in ("folder", "exposure", "setTemp", "readout", "gain", "offset", "binning")}
            client.post(f"/api/leaks/{t['id']}/sets", json={**body, "comment": note}).raise_for_status()
        t = _wait(client, t["id"])
        first, second = t["sets"]
        assert first["status"] == "done" and "vsPrevious" not in first
        assert second["vsPrevious"]["verdict"] == "better"

        c = client.get(f"/api/leaks/{t['id']}/compare", params={"base": first["id"], "comp": second["id"]}).json()
        assert c["verdict"] == "better" and first["result"]["structure"]["gradient"]["side"] == "left"
        img = client.get(f"/api/leaks/{t['id']}/diff.png", params={"base": first["id"], "comp": second["id"]})
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        assert client.get(f"/api/leaks/{t['id']}/sets/{first['id']}/master.png").status_code == 200

        t = client.patch(f"/api/leaks/{t['id']}/sets/{second['id']}", json={"comment": "Foil + tape"}).json()
        assert t["sets"][1]["comment"] == "Foil + tape"
        t = client.delete(f"/api/leaks/{t['id']}/sets/{first['id']}").json()
        assert [s["id"] for s in t["sets"]] == [second["id"]]
        assert client.get("/api/leaks").json()["tests"][0]["sets"] == 1
        assert client.delete(f"/api/leaks/{t['id']}").json()["ok"]
        assert client.get(f"/api/leaks/{t['id']}").status_code == 404
    # The frames are only read.
    assert sorted((p.name, p.stat().st_mtime) for p in root.rglob("*.fits")) == before
    assert not list((tmp_path / "var" / "leaks").glob("*.npz"))


def test_spot_check_tells_light_from_bias_steps(tmp_path):
    """Phase 1: one frame per flashlight spot. Light adds level and shot noise; a bias step only level."""
    root = tmp_path / "Astro"
    spots = root / "spots"
    write_darks(spots, 3, 1, imagetyp="LIGHT", filt="L")                                 # 0-2: no flashlight
    write_darks(spots, 1, 2, imagetyp="LIGHT", filt="L", start_min=3, light=2.0)         # 3: leaks
    write_darks(spots, 1, 3, imagetyp="LIGHT", filt="L", start_min=4)                    # 4: tight
    write_darks(spots, 1, 4, imagetyp="LIGHT", filt="L", start_min=5, step_every=1)      # 5: bias step
    write_darks(spots, 1, 5, imagetyp="LIGHT", filt="L", start_min=6, light=0.3)         # 6: faint leak
    cfg = Config(data_root=root, db_path=tmp_path / "var" / "db.sqlite")
    app = create_app(cfg, scan_on_start=False, observatory=False)
    with TestClient(app) as client:
        (g,) = client.get("/api/leaks/browse", params={"folder": "spots"}).json()["groups"]
        assert g["kind"] == "light" and g["filter"] == "L" and g["frames"] == 7
        t = client.post("/api/leaks", json={"name": "Flashlight"}).json()
        probe = {k: g[k] for k in ("folder", "exposure", "setTemp", "readout", "gain", "offset", "binning", "filter")}
        client.post(f"/api/leaks/{t['id']}/checks", json={"probe": probe, "comment": "First pass"}).raise_for_status()
        t = _wait(client, t["id"])
        (c,) = t["checks"]
        assert c["status"] == "done" and c["autoReference"]
        assert "path" not in c["frames"][0]
        # Nothing marked: the darkest frames by noise became the reference (bias steps do not add noise).
        assert [f["role"] for f in c["frames"]] == ["reference"] * 3 + ["probe", "reference", "reference", "probe"]

        # Mark what was really shot without the flashlight, and name the spots.
        frames = [{"index": i, "role": "reference" if i < 3 else "probe"} for i in range(7)]
        frames += [{"index": 3, "comment": "Focuser drawtube"}, {"index": 4, "comment": "Dew heater strap"},
                   {"index": 5, "comment": "Filter drawer"}, {"index": 6, "comment": "Camera tilt plate"}]
        t = client.patch(f"/api/leaks/{t['id']}/checks/{c['id']}", json={"frames": frames}).json()
        ev = t["checks"][0]["evaluation"]
        verdicts = [f["verdict"] for f in ev["frames"]]
        assert verdicts == ["reference"] * 3 + ["leak", "tight", "step", "leak"]
        assert ev["frames"][3]["levelDelta"] > 1.5 and ev["frames"][3]["noiseSignificant"]
        assert not ev["frames"][5]["noiseSignificant"] and ev["frames"][5]["levelSignificant"]
        assert t["checks"][0]["frames"][3]["comment"] == "Focuser drawtube"
        img = client.get(f"/api/leaks/{t['id']}/checks/{c['id']}/frames/3/diff.png")
        assert img.status_code == 200 and img.content[1:4] == b"PNG"
        assert client.delete(f"/api/leaks/{t['id']}/checks/{c['id']}").json()["checks"] == []
    assert not list((tmp_path / "var" / "leaks").glob("*.npz"))
