import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from astrodash.api import create_app
from astrodash.cache import HeaderCache
from astrodash.config import Config
from astrodash.library import Library
from astrodash.scanner import Scanner


def build(data_root: Path, tmp_path: Path) -> Library:
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "t.db")
    result = Scanner(data_root, HeaderCache(cfg.db_path), workers=2).scan()
    return Library(result, cfg)


def test_totals_exclusions_and_copies(data_root, tmp_path):
    lib = build(data_root, tmp_path)
    rev = lib.revision("ic-1848-soul-nebula")
    by_filter = {f["filter"]: f for f in rev["filters"]}
    # 3 H lights, one rejected via its _REJECTED copy; Accepted copy and [GARBAGE] session ignored
    assert by_filter["H"]["frames"] == 2 and by_filter["H"]["rejected"] == 1
    assert by_filter["O"]["frames"] == 2           # "OIII" header normalised to O
    assert rev["totals"]["nights"] == 2
    assert [e["folder"] for e in rev["excludedSessions"]] == ["[GARBAGE] 2025-12-27"]


def test_flat_status(data_root, tmp_path):
    rev = build(data_root, tmp_path).revision("ic-1848-soul-nebula")
    sessions = {s["folder"]: s for s in rev["sessions"]}
    assert sessions["NIGHT_2026-02-01"]["flatStatus"]["H"]["status"] == "ok"
    assert sessions["NIGHT_2026-02-02"]["flatStatus"]["O"]["status"] == "missing"
    assert any(c["code"] == "flats-missing" for c in rev["checks"])


def test_night_from_header_crosses_midnight(data_root, tmp_path):
    rev = build(data_root, tmp_path).revision("ic-1848-soul-nebula")
    night2 = next(s for s in rev["sessions"] if s["folder"] == "NIGHT_2026-02-02")
    # frames at 01:00 local on the 3rd belong to the night of the 2nd
    assert night2["headerNights"] == ["2026-02-02"] and not night2["nightMismatch"]


def test_legacy_mosaic_quarantine_and_revisions(data_root, tmp_path):
    lib = build(data_root, tmp_path)
    m31 = lib.revision("m-31-andromeda-galaxy")
    assert m31["panels"] == ["1", "2"]
    assert m31["legacyYears"] == ["2026"]
    assert m31["totals"]["lights"] == 2 and m31["totals"]["quarantined"] == 1
    assert m31["sessions"][0]["flatStatus"]["L"]["status"] == "ok"   # FlatWizard flats shared by panels
    assert [s["revision"] for s in m31["siblings"]] == [1, 2]
    groups = {g["id"]: g for g in lib.overview()["targets"]}
    assert len(groups["m-31"]["revisions"]) == 2


def test_dark_matching_infers_settings_from_raw_darks(data_root, tmp_path):
    lib = build(data_root, tmp_path)
    rev = lib.revision("ic-1848-soul-nebula")
    dark = rev["sessions"][0]["lightGroups"][0]["dark"]
    assert dark["status"] == "ok"
    assert dark["master"]["file"] == "MasterDark_-10C_300s.fits"
    assert dark["master"]["readout"] == "Linearity HDR" and dark["master"]["settingsKnown"]
    m31 = lib.revision("m-31-andromeda-galaxy")
    assert m31["sessions"][0]["lightGroups"][0]["dark"]["status"] == "missing"   # no 60 s master


def test_hdr_ignores_gain_offset_but_readout_must_match(data_root, tmp_path):
    rev = build(data_root, tmp_path).revision("ic-1848-soul-nebula")
    night2 = next(s for s in rev["sessions"] if s["folder"] == "NIGHT_2026-02-02")
    groups = {g["filter"]: g for g in night2["lightGroups"]}
    # gain 69 / offset 1 HDR lights match the master built from gain 9 / offset 100 HDR darks
    assert groups["O"]["gainIgnored"] and groups["O"]["gain"] is None
    assert groups["O"]["dark"]["status"] == "ok"
    # Full Resolution lights keep gain/offset and do not match an HDR master
    assert groups["S"]["gain"] == 80 and groups["S"]["dark"]["status"] == "missing"


def test_flat_readout_and_flat_calibration(data_root, tmp_path):
    lib = build(data_root, tmp_path)
    ic = lib.revision("ic-1848-soul-nebula")
    h = ic["sessions"][0]["flatStatus"]["H"]
    assert h["readoutWrong"] == [] and h["calibration"][0]["status"] == "ok"
    assert "session bias" in h["calibration"][0]["source"]
    m31 = lib.revision("m-31-andromeda-galaxy")
    l_flat = m31["sessions"][0]["flatStatus"]["L"]
    assert l_flat["readoutWrong"] == ["Linearity HDR"]
    codes = {c["code"]: c["level"] for c in m31["checks"]}
    assert codes["flats-readout"] == "bad"
    # Full Resolution flats + Linearity HDR bias/darks at another gain/offset is the intended workflow
    o = next(s for s in ic["sessions"] if s["folder"] == "NIGHT_2026-02-02")["flatStatus"]["S"]
    assert o["calibration"][0]["status"] == "ok" and "Linearity HDR" not in o["calibration"][0]["settings"]
    assert "flats-calibration" not in {c["code"] for c in ic["checks"]}


def test_flat_calibration_missing_without_any_bias(tmp_path):
    from .conftest import flat, light, write_fits
    root = tmp_path / "Astro"
    night = root / "X 1 (Test)" / ".SessionData" / "NIGHT_2026-01-01"
    write_fits(night / "LIGHT" / "L" / "l.fits", **light(FILTER="L"))
    write_fits(night / "FLAT" / "L" / "f.fits", **flat(FILTER="L"))
    rev = build(root, tmp_path).revision("x-1-test")
    assert {c["code"]: c["level"] for c in rev["checks"]}["flats-calibration"] == "warn"


def test_api_end_to_end(data_root, tmp_path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "api.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/targets").status_code == 503
        app.state.engine._run_scan()
        body = client.get("/api/targets").json()
        assert {t["catalogId"] for t in body["targets"]} == {"IC 1848", "M 31"}
        assert client.get("/api/revisions/ic-1848-soul-nebula").status_code == 200
        assert client.get("/api/revisions/nope").status_code == 404
        assert client.get("/api/calibration").json()["builds"][0]["build"] == "2026-09-17"


def test_scan_does_not_modify_data_root(data_root, tmp_path):
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in data_root.rglob("*")}
    build(data_root, tmp_path)
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in data_root.rglob("*")}
    assert before == after


def test_short_bias_is_suspect_and_trusted_bias_preferred(data_root, tmp_path):
    lib = build(data_root, tmp_path)
    cal = lib.calibration()
    assert [m["file"] for m in cal["suspectMasters"]] == ["masterBias_-10.fits"]   # EXPTIME 0.0
    raw_bias = [g for b in cal["builds"] for g in b["raw"] if g["kind"] == "bias"]
    assert raw_bias and all(g["suspect"] is None for g in raw_bias)                 # 0.25 s is fine
    s = next(x for x in lib.revision("ic-1848-soul-nebula")["sessions"] if x["folder"] == "NIGHT_2026-02-02")
    flat_cal = s["flatStatus"]["S"]["calibration"][0]
    assert flat_cal["status"] == "ok" and "raw bias" in flat_cal["source"]


def test_flats_with_only_suspect_bias_warn(tmp_path):
    from .conftest import flat, light, write_fits
    root = tmp_path / "Astro"
    night = root / "X 1 (Test)" / ".SessionData" / "NIGHT_2026-01-01"
    write_fits(night / "LIGHT" / "L" / "l.fits", **light(FILTER="L"))
    write_fits(night / "FLAT" / "L" / "f.fits", **flat(FILTER="L"))
    write_fits(root / ".Calibration" / "2026-01-02" / "BIAS" / "b.fits", IMAGETYP="BIAS", EXPOSURE=0.000001,
               GAIN=9, OFFSET=100, READOUTM="Linearity HDR", SET_TEMP=-10.0, INSTRUME="Cam")
    rev = build(root, tmp_path).revision("x-1-test")
    checks = {c["code"]: c for c in rev["checks"]}
    assert checks["flats-calibration-suspect"]["level"] == "warn"
    assert "1 µs" in checks["flats-calibration-suspect"]["detail"][0]


def test_hdr_flats_not_reported_on_finished_targets(data_root, tmp_path):
    (data_root / "M 31 (Andromeda Galaxy)" / "2026" / ".Finished" / "m31.jpg").write_bytes(b"x")
    m31 = build(data_root, tmp_path).revision("m-31-andromeda-galaxy")
    assert m31["stage"]["value"] == "finished"
    assert "flats-readout" not in {c["code"] for c in m31["checks"]}
    assert m31["sessions"][0]["flatStatus"]["L"]["readoutAccepted"] == ["Linearity HDR"]


def test_dismiss_and_restore_dark_issue(data_root, tmp_path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "dismiss.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        unmatched = client.get("/api/calibration").json()["unmatched"]
        row = next(u for u in unmatched if u["exposure"] == 60.0)          # M 31's 60 s lights
        assert row["status"] == "missing"

        r = client.post("/api/dismissals", json={"key": row["key"], "label": "60 s", "note": "used the 120 s dark"})
        assert r.status_code == 200
        row = next(u for u in client.get("/api/calibration").json()["unmatched"] if u["key"] == row["key"])
        assert row["status"] == "dismissed" and row["was"] == "missing" and row["note"] == "used the 120 s dark"
        m31 = client.get("/api/revisions/m-31-andromeda-galaxy").json()
        codes = {c["code"]: c for c in m31["checks"]}
        assert "darks-missing" not in codes and codes["darks-dismissed"]["level"] == "info"
        assert "used the 120 s dark" in codes["darks-dismissed"]["detail"][0]

        assert client.request("DELETE", "/api/dismissals", params={"key": row["key"]}).json() == {"ok": True}
        m31 = client.get("/api/revisions/m-31-andromeda-galaxy").json()
        assert "darks-missing" in {c["code"] for c in m31["checks"]}
    # Dismissals live in the dashboard's own database, never in the data root.
    assert not any("dismiss" in p.name for p in data_root.rglob("*"))


def _calib_root(tmp_path):
    from .conftest import write_fits
    root = tmp_path / "Astro"
    lib = root / ".Calibration" / "2026-09-17"
    for i in range(3):   # 0.25 s biases at 05:03–05:05 UTC (the real inputs)
        write_fits(lib / "BIAS" / "0.25s" / f"b{i}.fits", IMAGETYP="BIAS", EXPOSURE=0.25, GAIN=69, OFFSET=1,
                   READOUTM="Linearity HDR", SET_TEMP=-10.0, INSTRUME="Cam", DATE_OBS=f"2026-09-17T05:0{3 + i}:00")
    for i in range(3):   # 1 µs biases shot earlier the same night at the same temperature
        write_fits(lib / "BIAS" / "Dark" / f"d{i}.fits", IMAGETYP="BIAS", EXPOSURE=0.000001, GAIN=9, OFFSET=100,
                   READOUTM="Linearity HDR", SET_TEMP=-10.0, INSTRUME="Cam", DATE_OBS=f"2026-09-17T03:0{i}:00")
    return root, lib


def test_master_bias_exposure_from_its_inputs_not_exptime(tmp_path):
    from .conftest import write_fits
    root, lib = _calib_root(tmp_path)
    # WBPP writes EXPTIME = 0 on every master bias; DATE-OBS..DATE-END spans the 0.25 s inputs
    write_fits(lib / "Masters" / "-10.0C" / "masterBias_-10.fits", IMAGETYP="Master Bias", EXPTIME=0.0,
               INSTRUME="Cam", DATE_OBS="2026-09-17T05:03:00", DATE_END="2026-09-17T05:05:00")
    cal = build(root, tmp_path).calibration()
    master = next(m for b in cal["builds"] for m in b["masters"])
    assert master["exposure"] == 0.25 and master["sources"] == 3 and master["suspect"] is None
    assert master["readout"] == "Linearity HDR" and master["settingsKnown"]
    assert cal["suspectMasters"] == []


def test_master_bias_exposure_from_xisf_property(tmp_path):
    root, lib = _calib_root(tmp_path)
    xml = (
        '<?xml version="1.0"?><xisf><Image geometry="4:4:1" imageType="MasterBias">'
        '<FITSKeyword name="EXPTIME" value="0.000" comment=""/>'
        '<Property id="Instrument:FrameExposureTime" type="Float64" value="0.25"/></Image></xisf>'
    ).encode()
    p = lib / "Masters" / "-10.0C" / "masterBias_-10.xisf"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"XISF0100" + len(xml).to_bytes(4, "little") + b"\0" * 4 + xml)
    master = next(m for b in build(root, tmp_path).calibration()["builds"] for m in b["masters"])
    assert master["exposure"] == 0.25 and master["exposureFrom"] == "XISF FrameExposureTime"
    assert master["suspect"] is None


def test_an_ignored_session_stays_listed_but_leaves_the_totals(data_root, tmp_path):
    app = create_app(Config(data_root=data_root, db_path=tmp_path / "var" / "ign.db"), scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        url = "/api/revisions/ic-1848-soul-nebula"
        before = client.get(url).json()
        night2 = next(s for s in before["sessions"] if s["folder"] == "NIGHT_2026-02-02")
        assert night2["ignored"] is False
        after = client.put(f"{url}/sessions/NIGHT_2026-02-02/ignored", json={"ignored": True}).json()
        s2 = next(s for s in after["sessions"] if s["folder"] == "NIGHT_2026-02-02")
        assert s2["ignored"] is True and s2["totals"] == night2["totals"]  # still listed, with its own numbers
        assert after["totals"]["seconds"] == before["totals"]["seconds"] - night2["totals"]["seconds"]
        assert after["totals"]["nights"] == before["totals"]["nights"] - 1
        assert after["totals"]["ignoredNights"] == 1 and after["totals"]["ignoredSeconds"] == night2["totals"]["seconds"]
        assert after["totals"]["last"] == "2026-02-01"
        assert {f["filter"] for f in after["filters"]} == {"H"}  # night 2's O and S are gone from the filters
        assert sum(f["acceptedSeconds"] for f in after["filters"]) == after["totals"]["seconds"]
        # Its flats/darks issues no longer count; the overview follows.
        ov = next(t for t in client.get("/api/targets").json()["targets"] if t["catalogId"] == "IC 1848")
        assert ov["revisions"][0]["totals"]["seconds"] == after["totals"]["seconds"]
        # A rescan keeps the decision; bringing the night back restores the totals.
        app.state.engine._run_scan()
        assert client.get(url).json()["totals"]["ignoredNights"] == 1
        back = client.put(f"{url}/sessions/NIGHT_2026-02-02/ignored", json={"ignored": False}).json()
        assert back["totals"] == {**before["totals"]}
        assert client.put(f"{url}/sessions/NIGHT_1999-01-01/ignored", json={"ignored": True}).status_code == 404


def test_acquired_time_counts_set_aside_lights_once(data_root, tmp_path):
    rev = build(data_root, tmp_path).revision("ic-1848-soul-nebula")
    night = next(s for s in rev["sessions"] if s["folder"] == "NIGHT_2026-02-01")
    t = night["totals"]
    # 3 H lights: h2 rejected through its _REJECTED copy (counted once), the Accepted copy not at all.
    assert t["lights"] == 2 and t["rejected"] == 1
    assert t["acquiredSeconds"] == t["seconds"] * 3 / 2
    assert rev["totals"]["acquiredSeconds"] == sum(s["totals"]["acquiredSeconds"] for s in rev["sessions"])


def _archive_soul(data_root: Path, tmp_path: Path) -> Path:
    """Move the Soul Nebula target folder to an "archive disk" beside the data root."""
    archive = tmp_path / "Archive"
    archive.mkdir()
    shutil.move(str(data_root / "IC 1848 (Soul Nebula)"), str(archive / "IC 1848 (Soul Nebula)"))
    return archive


def test_targets_in_an_archive_folder_stay_in_the_library(data_root, tmp_path):
    archive = _archive_soul(data_root, tmp_path)
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "a.db", archive_roots=[archive, tmp_path / "Unplugged"])
    result = Scanner(data_root, HeaderCache(cfg.db_path), workers=2, archive_roots=lambda: cfg.archive_roots).scan()
    lib = Library(result, cfg)
    rev = lib.revision("ic-1848-soul-nebula")
    assert rev is not None and rev["archived"] and rev["root"] == str(archive).replace("\\", "/")
    assert {f["filter"]: f["frames"] for f in rev["filters"]}["H"] == 2
    assert not any(r["archived"] for r in lib.revisions.values() if r["id"] != rev["id"])
    roots = {Path(r["path"]): r for r in lib.roots}
    assert roots[archive]["exists"] and roots[archive]["targets"] == 1
    assert not roots[tmp_path / "Unplugged"]["exists"]
    # Diagnostics keys stay "Target/…" whichever disk the target is on, and lead back to the file.
    key = next(k for k in lib.light_headers if k.startswith("IC 1848 (Soul Nebula)/"))
    assert cfg.locate(key) == archive / key and cfg.locate(key).is_file()
    assert cfg.rel_path(archive / key) == key and cfg.root_of(archive / key) == archive
    assert cfg.locate("Elsewhere/x.fits") == data_root / "Elsewhere/x.fits"


def test_a_target_folder_in_two_roots_is_read_from_the_first(data_root, tmp_path):
    archive = tmp_path / "Archive"
    shutil.copytree(data_root / "IC 1848 (Soul Nebula)", archive / "IC 1848 (Soul Nebula)")   # copied, not moved yet
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "d.db", archive_roots=[archive])
    result = Scanner(data_root, HeaderCache(cfg.db_path), workers=2, archive_roots=lambda: cfg.archive_roots).scan()
    lib = Library(result, cfg)
    assert not lib.revision("ic-1848-soul-nebula")["archived"]
    assert lib.roots[1]["skipped"] == ["IC 1848 (Soul Nebula)"] and lib.roots[1]["targets"] == 0


def test_archive_folders_setting(data_root, tmp_path):
    archive = _archive_soul(data_root, tmp_path)
    app = create_app(Config(data_root=data_root, db_path=tmp_path / "var" / "s.db"), scan_on_start=False)
    with TestClient(app) as client:
        engine = app.state.engine
        engine._run_scan()
        assert "ic-1848-soul-nebula" not in engine.library.revisions
        put = lambda dirs: client.put("/api/settings", json={"library": {"archiveDirs": dirs}})  # noqa: E731
        for bad in ([str(tmp_path / "missing")], ["Archive"], [str(data_root)], [str(data_root / "IC 1848")],
                    [str(tmp_path)], [str(archive), str(archive) + "\\"]):
            assert put(bad).status_code == 422, bad
        saved = put([f'"{archive}\\"', " "]).json()["settings"]["library"]
        assert saved == {"archiveDirs": [str(archive)]}
        engine.rescan()
        assert engine.library.revision("ic-1848-soul-nebula")["archived"]
        roots = client.get("/api/library/roots").json()["roots"]
        assert [r["archive"] for r in roots] == [False, True] and roots[1]["targets"] == 1
        # The organizer never writes on an archive disk.
        assert client.post("/api/organizer/plans", json={"revisionId": "ic-1848-soul-nebula"}).status_code == 409
        assert all(r["revisionId"] != "ic-1848-soul-nebula" for r in client.get("/api/organizer").json()["revisions"])
        # A saved folder on an unplugged disk can stay in the list.
        shutil.rmtree(archive)
        assert put([str(archive)]).status_code == 200
        assert client.put("/api/settings", json={"library": {"archiveDirs": []}}).json()["settings"]["library"] == {"archiveDirs": []}
