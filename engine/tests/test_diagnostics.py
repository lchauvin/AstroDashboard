"""Diagnostics: real astro-eval on a synthetic star field, the job API, previews."""

import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from astrodash.api import create_app
from astrodash.config import Config

ASTRO_EVAL = Config().astro_eval_path
needs_astro_eval = pytest.mark.skipif(not (ASTRO_EVAL / "astro_eval").is_dir(), reason="astro-eval checkout not found")


def _write_image(path: Path, data: np.ndarray, **header) -> None:
    from astropy.io import fits

    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(data.astype(np.uint16))
    for k, v in header.items():
        hdu.header[k.replace("_", "-")] = v
    hdu.writeto(path)


def _star_field(rng, n_stars=120, fwhm=3.0, amp=3000.0, bg=800.0, shape=(400, 600)):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    img = np.full(shape, bg, dtype=np.float64)
    sigma = fwhm / 2.3548
    star_rng = np.random.default_rng(42)          # same stars in every frame
    for _ in range(n_stars):
        y, x = star_rng.uniform(10, shape[0] - 10), star_rng.uniform(10, shape[1] - 10)
        a = amp * star_rng.uniform(0.2, 1.0)
        y0, x0 = int(y), int(x)
        sl = (slice(max(y0 - 12, 0), y0 + 12), slice(max(x0 - 12, 0), x0 + 12))
        img[sl] += a * np.exp(-((xx[sl] - x) ** 2 + (yy[sl] - y) ** 2) / (2 * sigma**2))
    return rng.poisson(img).astype(np.float64)


@pytest.fixture()
def diag_root(tmp_path: Path) -> Path:
    root = tmp_path / "Astro"
    night = root / "T 1 (Test)" / ".SessionData" / "NIGHT_2026-01-01"
    rng = np.random.default_rng(1)
    common = dict(EXPOSURE=120.0, GAIN=9, OFFSET=100, READOUTM="Linearity HDR", XBINNING=1, SET_TEMP=-10.0,
                  INSTRUME="Cam", FOCALLEN=250.0, XPIXSZ=2.9, FOCPOS=1000, SITELAT=45.5, SITELONG=-73.5, OBJECT="T 1")
    for i in range(8):
        cloudy = i == 7
        img = _star_field(rng, amp=600.0 if cloudy else 3000.0, bg=2500.0 if cloudy else 800.0)
        _write_image(night / "LIGHT" / "L" / f"l{i}.fits", img, IMAGETYP="LIGHT", FILTER="L",
                     DATE_OBS=f"2026-01-02T03:{10 + 3 * i:02d}:00", DATE_LOC=f"2026-01-01T22:{10 + 3 * i:02d}:00", **common)
    # an astro-eval "Accepted" copy and a Snapshot frame: neither may be measured
    import shutil
    (night / "LIGHT" / "L" / "Accepted").mkdir()
    shutil.copy(night / "LIGHT" / "L" / "l0.fits", night / "LIGHT" / "L" / "Accepted" / "l0.fits")
    (night / "Snapshot" / "SNAPSHOT" / "L").mkdir(parents=True)
    shutil.copy(night / "LIGHT" / "L" / "l1.fits", night / "Snapshot" / "SNAPSHOT" / "L" / "s.fits")
    for i in range(6):
        flat = rng.poisson(np.full((400, 600), 26000.0)).astype(np.float64)
        _write_image(night / "FLAT" / "L" / f"f{i}.fits", flat, IMAGETYP="FLAT", FILTER="L", EXPOSURE=2.0, GAIN=9,
                     OFFSET=100, READOUTM="Full Resolution", XBINNING=1, FOCPOS=1000, XPIXSZ=2.9, FOCALLEN=250.0,
                     DATE_OBS=f"2026-01-02T10:0{i}:00")
    return root


@needs_astro_eval
def test_runner_measures_frames_and_writes_nothing(diag_root, tmp_path):
    from astrodash.diagnostics.runner import run_session

    before = {p: p.stat().st_mtime_ns for p in diag_root.rglob("*")}
    session = diag_root / "T 1 (Test)" / ".SessionData" / "NIGHT_2026-01-01"
    res = run_session(session, diag_root, ASTRO_EVAL, site=(45.5, -73.5), workers=2)
    assert res["errors"] == [] and res["measured"] == 14
    (light,) = res["lights"]
    assert light["filter"] == "L" and light["summary"]["frames"] == 8
    frames = {f["file"]: f for f in light["frames"]}
    assert frames["l0.fits"]["n_stars"] > 50 and frames["l0.fits"]["fwhm_median"] is not None
    assert frames["l7.fits"]["rejected"]                      # the "cloudy" frame
    assert frames["l0.fits"]["sunAlt"] is not None
    assert res["flats"][0]["summary"]["verdict"] in ("good", "warn", "bad")
    assert all("Flats use a different" not in i["text"] for r in res["calibration"]["rows"] for i in r["issues"])
    after = {p: p.stat().st_mtime_ns for p in diag_root.rglob("*")}
    assert before == after, "diagnostics must not create or modify anything in the data root"


def test_diagnostics_jobs_api(data_root, tmp_path):
    calls = []

    def fake_runner(session_dir, data_root_, astro_eval_path, site=None, workers=None, progress=None):
        calls.append(session_dir)
        progress("measuring", 1, 1)
        return {"lights": [{"key": "H", "filter": "H", "summary": {"frames": 3, "kept": 2, "rejected": 1,
                                                                   "keptSeconds": 600, "median": {"fwhm_median": 3.1}},
                            "frames": []}],
                "flats": [], "calibration": {"rows": []}, "errors": []}

    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "diag.db")
    app = create_app(cfg, scan_on_start=False, diagnostics_runner=fake_runner)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        assert client.post("/api/diagnostics/run", json={"revisionId": "nope"}).status_code == 404
        jobs = client.post("/api/diagnostics/run", json={"revisionId": "ic-1848-soul-nebula"}).json()["jobs"]
        assert {j["session"] for j in jobs} == {"NIGHT_2026-02-01", "NIGHT_2026-02-02"}
        for _ in range(100):
            if all(j["status"] == "done" for j in client.get("/api/diagnostics").json()["jobs"]):
                break
            time.sleep(0.05)
        body = client.get("/api/diagnostics").json()
        assert {r["session"] for r in body["results"]} == {"NIGHT_2026-02-01", "NIGHT_2026-02-02"}
        assert body["results"][0]["summary"]["lights"][0]["kept"] == 2
        detail = client.get("/api/diagnostics/ic-1848-soul-nebula/NIGHT_2026-02-01").json()
        assert detail["stored"]["result"]["lights"][0]["key"] == "H"
        assert detail["session"]["path"].endswith("NIGHT_2026-02-01")
    assert all(Path(c).name.startswith("NIGHT_") for c in calls)


def test_diagnostics_run_brings_the_library_up_to_date(data_root, tmp_path):
    def fake_runner(session_dir, data_root_, astro_eval_path, site=None, workers=None, progress=None):
        return {"lights": [], "flats": [], "calibration": {"rows": []}, "errors": []}

    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "fresh.db")
    app = create_app(cfg, scan_on_start=False, diagnostics_runner=fake_runner)
    rev, night = "ic-1848-soul-nebula", "NIGHT_2026-02-01"
    with TestClient(app) as client:
        app.state.engine._run_scan()
        flat_status = lambda: next(s for s in client.get(f"/api/revisions/{rev}").json()["sessions"]  # noqa: E731
                                   if s["folder"] == night)["flatStatus"]["H"]["status"]
        assert flat_status() == "ok"
        # The user removes the night's flats on disk, then reruns its diagnostic.
        (data_root / "IC 1848 (Soul Nebula)" / ".SessionData" / night / "FLAT" / "H" / "f0.fits").unlink()
        client.post("/api/diagnostics/run", json={"revisionId": rev, "session": night})
        for _ in range(200):
            if all(j["status"] == "done" for j in client.get("/api/diagnostics").json()["jobs"]):
                break
            time.sleep(0.05)
        assert flat_status() == "missing"


def test_preview_refuses_paths_outside_data_root(data_root, tmp_path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "p.db")
    with TestClient(create_app(cfg, scan_on_start=False)) as client:
        assert client.get("/api/frames/preview.jpg", params={"path": "../../etc/passwd"}).status_code == 404
        ok = client.get("/api/frames/preview.jpg",
                        params={"path": "IC 1848 (Soul Nebula)/.SessionData/NIGHT_2026-02-01/LIGHT/H/h0.fits"})
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"


def _vignetted(shape=(800, 1200), dust=((300, 400), (500, 900))):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    r = np.hypot((yy - shape[0] / 2) / shape[0], (xx - shape[1] / 2) / shape[1])
    img = 1.0 - 0.6 * r**2                           # vignetting
    for cy, cx in dust:                               # dust donuts: 4 % dips, ~40 px across
        d = np.hypot(yy - cy, xx - cx)
        img *= 1 - 0.04 * np.exp(-((d - 20) ** 2) / (2 * 6**2))
    return img


def test_flat_match_detects_moved_dust(tmp_path):
    from astrodash.diagnostics.flatmatch import compare

    rng = np.random.default_rng(3)
    flat = _vignetted()
    same = _vignetted()
    moved = _vignetted(dust=((150, 200), (650, 300)))
    paths = {}
    for name, base, level in (("flat", flat, 30000), ("same", same, 2000), ("moved", moved, 2000)):
        paths[name] = []
        for i in range(4):
            p = tmp_path / name / f"{i}.fits"
            _write_image(p, rng.poisson(base * level).astype(np.float64))
            paths[name].append(str(p))

    good = compare(paths["same"], paths["flat"])
    assert good["verdict"] == "ok" and 0.7 < good["dustInLights"] < 1.3, good
    assert abs(good["vignettingCorrected"]) < 0.05

    bad = compare(paths["moved"], paths["flat"])
    assert bad["verdict"] == "warn", bad
    assert any("dust" in n for n in bad["notes"])


def test_hdr_flat_notes_hidden_on_finished_targets(data_root, tmp_path):
    note = {"level": "warn", "text": "L flats · 2 s: Readout mode is Linearity HDR; flats should be acquired in Full Resolution mode."}

    def fake_runner(session_dir, data_root_, astro_eval_path, site=None, workers=None, progress=None):
        return {"lights": [], "errors": [],
                "flats": [{"key": "L flats · 2 s", "filter": "L", "frames": [],
                           "summary": {"verdict": "warn", "notes": [note]}}],
                "calibration": {"rows": [{"light": "L", "filter": "L", "n": 2, "status": "warn", "flat": "L flats · 2 s",
                                          "issues": [note]}], "common": [], "orphans": [], "counts": {}}}

    (data_root / "M 31 (Andromeda Galaxy)" / "2026" / ".Finished" / "m31.jpg").write_bytes(b"x")
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "rules.db")
    app = create_app(cfg, scan_on_start=False, diagnostics_runner=fake_runner)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        for rev, sess in (("m-31-andromeda-galaxy", "NIGHT_2026-08-20"), ("ic-1848-soul-nebula", "NIGHT_2026-02-01")):
            client.post("/api/diagnostics/run", json={"revisionId": rev, "session": sess})
        for _ in range(100):
            if all(j["status"] == "done" for j in client.get("/api/diagnostics").json()["jobs"]):
                break
            time.sleep(0.05)
        finished = client.get("/api/diagnostics/m-31-andromeda-galaxy/NIGHT_2026-08-20").json()["stored"]["result"]
        assert finished["calibration"]["rows"][0]["issues"] == [] and finished["calibration"]["rows"][0]["status"] == "ok"
        assert finished["flats"][0]["summary"]["verdict"] == "good"
        acquiring = client.get("/api/diagnostics/ic-1848-soul-nebula/NIGHT_2026-02-01").json()["stored"]["result"]
        assert acquiring["calibration"]["rows"][0]["issues"] == [note]      # still reported where it matters
