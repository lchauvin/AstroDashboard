"""Auto-ingest (PLAN §5.1) on a synthetic data root. Never touches G:\\."""

import os

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astrodash.api import create_app
from astrodash.config import Config
from astrodash.ingest import fileops

from .conftest import flat, light, write_fits

# Tonight's evening date: an older night would already be "closed" (its next noon has passed).
EVENING = (datetime.now() - timedelta(hours=12)).date()
NIGHT = EVENING.isoformat()
NEXT = (EVENING + timedelta(days=1)).isoformat()
FAR = (EVENING + timedelta(days=4)).isoformat()
OLD = time.time() - 3600


def frame(path: Path, **header) -> Path:
    write_fits(path, **header)
    os.utime(path, (OLD, OLD))
    return path


@pytest.fixture()
def download(data_root: Path) -> Path:
    dl = data_root / ".Download" / NIGHT
    for i in range(3):
        f = frame(dl / "Soul Nebula" / "LIGHT" / "Ha" / f"s{i}.fits",
                  **light(FILTER="Ha", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:0{i}:00"))
        os.utime(f, (OLD + i, OLD + i))  # s2 is the newest
    frame(dl / "M 31 Panel 2" / "LIGHT" / "L" / "m0.fits", **light(FILTER="L", OBJECT="M 31 Panel 2", DATE_LOC=f"{NEXT}T01:00:00"))
    frame(dl / "FlatWizard" / "FLAT" / "Ha" / "f0.fits", **flat(FILTER="Ha", DATE_LOC=f"{NEXT}T06:30:00"))
    frame(dl / "Snapshot" / "snap.fits", **light(FILTER="L", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T21:00:00"))
    frame(dl / "DARK" / "d0.fits", IMAGETYP="DARK", EXPOSURE=300.0, DATE_LOC=f"{NIGHT}T21:00:00")
    return dl


def app_for(data_root: Path, tmp_path: Path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "ingest.db")
    app = create_app(cfg, scan_on_start=False)
    return app


def test_plan_moves_holds_and_skips(data_root, download, tmp_path):
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = app.state.engine.ingest.plan()
        moves = {Path(a.src).name: a for a in plan.actions}
        # Soul Nebula → IC 1848 by name; the newest light stays for the preview
        assert {"s0.fits", "s1.fits"} <= set(moves) and "s2.fits" not in moves
        s0 = moves["s0.fits"]
        assert s0.dst.replace("\\", "/").endswith(f"IC 1848 (Soul Nebula)/.SessionData/NIGHT_{NIGHT}/LIGHT/H/s0.fits")
        # After midnight still the evening's night; mosaic panel → M31_Panel_2 in the acquiring revision
        m0 = moves["m0.fits"]
        assert f"/NIGHT_{NIGHT}/M31_Panel_2/LIGHT/L/" in m0.dst.replace("\\", "/") and "R2" in m0.target
        # Flats follow the night's targets: one move, one copy
        flat_ops = sorted(a.op for a in plan.actions if a.kind == "FLAT")
        assert flat_ops == ["copy", "move"]
        reasons = {Path(h.src).name: h.reason for h in plan.holds}
        assert reasons["s2.fits"] == "newest"
        skipped = {Path(h.src).name for h in plan.skipped}
        assert {"snap.fits", "d0.fits"} <= skipped
        # Dry-run (the default) moves nothing
        assert client.get("/api/ingest").json()["mode"] == "dry-run"
        client.post("/api/ingest/run")
        assert (download / "Soul Nebula" / "LIGHT" / "Ha" / "s0.fits").exists()


def test_auto_moves_journals_closes_and_undoes(data_root, download, tmp_path):
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        client.post("/api/ingest/mode", json={"mode": "auto"})
        run = client.post("/api/ingest/run").json()
        assert run["moved"] >= 4 and run["copied"] == 1 and run["errors"] == 0
        dest = data_root / "IC 1848 (Soul Nebula)" / ".SessionData" / f"NIGHT_{NIGHT}"
        assert (dest / "LIGHT" / "H" / "s0.fits").is_file()
        assert (download / "Soul Nebula" / "LIGHT" / "Ha" / "s2.fits").is_file()  # newest kept
        # The copy and the move of the flat both exist; the source is gone (moved, not deleted)
        assert (dest / "FLAT" / "H" / "f0.fits").is_file()
        assert not (download / "FlatWizard" / "FLAT" / "Ha" / "f0.fits").exists()

        client.post("/api/ingest/close", json={"night": NIGHT})
        client.post("/api/ingest/run")
        assert (dest / "LIGHT" / "H" / "s2.fits").is_file()
        status = client.get("/api/ingest").json()
        assert status["journal"][NIGHT]["moved"] >= 5 and status["journal"][NIGHT]["emptyDirs"] >= 1

        # Cleanup needs explicit confirmation, and only removes empty folders
        assert client.post("/api/ingest/cleanup", json={"night": NIGHT}).status_code == 422
        removed = client.post("/api/ingest/cleanup", json={"night": NIGHT, "confirm": True}).json()["removed"]
        assert removed and (download / "DARK" / "d0.fits").is_file() and (download / "Snapshot" / "snap.fits").is_file()

        # Undo moves everything back (still in Auto); the flat copy is reported, never deleted
        undo = client.post("/api/ingest/undo", json={"night": NIGHT}).json()
        assert undo["movedBack"] >= 5 and len(undo["copiesLeft"]) == 1 and not undo["failed"]
        assert (download / "Soul Nebula" / "LIGHT" / "Ha" / "s0.fits").is_file()
        assert not (dest / "LIGHT" / "H" / "s0.fits").exists()
        assert Path(undo["copiesLeft"][0]).is_file()
        # The undone night is paused: Auto does not redo it on the next pass…
        assert client.get("/api/ingest").json()["paused"] == [NIGHT]
        assert client.post("/api/ingest/run").json()["moved"] == 0
        assert (download / "Soul Nebula" / "LIGHT" / "Ha" / "s0.fits").is_file()
        # …until you resume it.
        client.post("/api/ingest/resume", json={"night": NIGHT})
        # (the night is open again, so the newest light waits; the flat clashes with its own leftover copy)
        assert client.post("/api/ingest/run").json()["moved"] == 3


def test_unknown_object_is_held_until_mapped(data_root, tmp_path):
    dl = data_root / ".Download" / NIGHT
    for i in range(2):
        f = frame(dl / "Mystery" / "LIGHT" / "H" / f"x{i}.fits", **light(FILTER="H", OBJECT="Mystery", DATE_LOC=f"{NIGHT}T23:0{i}:00"))
        os.utime(f, (OLD + i, OLD + i))  # x1 is the newest
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        holds = app.state.engine.ingest.plan().holds
        assert any(h.reason == "unmapped" and h.prompt and h.object == "Mystery" for h in holds)
        assert client.put("/api/ingest/map", json={"object": "Mystery", "revisionId": "ic-1848-soul-nebula"}).json()["ok"]
        plan = app.state.engine.ingest.plan()
        assert any(Path(a.src).name == "x0.fits" and "IC 1848" in a.dst for a in plan.actions)
        mapped = client.get("/api/ingest").json()["objectMap"]
        assert any(m["object"] == "Mystery" and not m["learned"] for m in mapped)


def test_not_ready_and_conflicts_are_held(data_root, tmp_path):
    dl = data_root / ".Download" / NIGHT / "Soul Nebula" / "LIGHT"
    fresh = dl / "H" / "fresh.fits"
    write_fits(fresh, **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:00:00"))  # just written
    short = frame(dl / "H" / "short.fits", **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:01:00"))
    with open(short, "r+b") as fh:  # simulate a transfer still in progress (test fixture only)
        fh.truncate(2880)
    os.utime(short, (OLD, OLD))
    frame(dl / "O" / "wrong.fits", **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:02:00"))
    frame(dl / "H" / "far.fits", **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{FAR}T22:03:00"))
    existing = data_root / "IC 1848 (Soul Nebula)" / ".SessionData" / f"NIGHT_{NIGHT}" / "LIGHT" / "H" / "dup.fits"
    write_fits(existing, **light(FILTER="H"))
    frame(dl / "H" / "dup.fits", **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:04:00"))
    frame(dl / "H" / "newer.fits", **light(FILTER="H", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:05:00"))
    app = app_for(data_root, tmp_path)
    with TestClient(app):
        app.state.engine._run_scan()
        reasons = {Path(h.src).name: h.reason for h in app.state.engine.ingest.plan().holds}
    assert reasons["fresh.fits"] in ("settling", "newest")
    assert reasons["short.fits"] == "incomplete"
    assert reasons["wrong.fits"] == "filter-mismatch"
    assert reasons["far.fits"] == "date-mismatch"
    assert reasons["dup.fits"] == "exists"


def test_fileops_refuses_anything_outside_its_lane(data_root, tmp_path):
    src = frame(data_root / ".Download" / NIGHT / "x.fits", **light())
    session = data_root / "IC 1848 (Soul Nebula)" / ".SessionData"
    with pytest.raises(fileops.UnsafeOperation):  # destination not in a NIGHT_ folder
        fileops.move(data_root, str(src), str(session / "x.fits"))
    with pytest.raises(fileops.UnsafeOperation):  # source not in .Download
        fileops.move(data_root, str(session / "NIGHT_2026-02-01" / "LIGHT" / "H" / "h0.fits"), str(session / "NIGHT_2026-03-01" / "x.fits"))
    with pytest.raises(fileops.UnsafeOperation):  # outside the data root
        fileops.move(data_root, str(src), str(tmp_path / "elsewhere" / ".SessionData" / "NIGHT_2026-01-01" / "x.fits"))
    existing = session / "NIGHT_2026-02-01" / "LIGHT" / "H" / "h0.fits"
    with pytest.raises(FileExistsError):  # never overwrite
        fileops.move(data_root, str(src), str(existing))
    with pytest.raises(fileops.UnsafeOperation):  # rmdir only directly on a night folder
        fileops.rmdir_empty(data_root, str(session))
    assert src.is_file()


def test_only_the_ingest_service_calls_fileops():
    pkg = Path(__file__).resolve().parents[1] / "astrodash"

    def imports_fileops(text: str) -> bool:
        return any(line.lstrip().startswith(("from ", "import ")) and "fileops" in line for line in text.splitlines())

    users = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py") if imports_fileops(p.read_text(encoding="utf-8"))]
    assert users == ["ingest/service.py"]


def test_tonights_target_routes_every_light_and_warns_on_finished(data_root, download, tmp_path):
    # An object nothing maps to: normally held with a prompt.
    frame(download / "Mystery" / "LIGHT" / "Ha" / "x0.fits", **light(FILTER="Ha", OBJECT="Mystery", DATE_LOC=f"{NIGHT}T23:00:00"))
    # A revision declared finished in its target.json.
    (data_root / "M 31 (Andromeda Galaxy) R2" / "target.json").write_text(
        '{"catalog_id": "M 31", "name": "Andromeda Galaxy", "revision": 2, "status": "finished"}', encoding="utf-8")
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        svc = app.state.engine.ingest
        lib = app.state.engine.library
        rid = {r["folder"]: r["id"] for r in lib.revisions.values()}
        held = {Path(h.src).name: h.reason for h in svc.plan().holds}
        assert held["x0.fits"] == "unmapped"

        # Every light of tonight → IC 1848, whatever its OBJECT says.
        r = client.post("/api/ingest/tonight", json={"revisionId": rid["IC 1848 (Soul Nebula)"]})
        assert r.status_code == 200
        plan = svc.plan()
        moves = {Path(a.src).name: a for a in plan.actions if a.kind == "LIGHT"}
        assert moves["x0.fits"].dst.replace("\\", "/").endswith(f"IC 1848 (Soul Nebula)/.SessionData/NIGHT_{NIGHT}/LIGHT/H/x0.fits")
        assert "IC 1848" in moves["m0.fits"].target  # after midnight, still tonight
        seen = plan.objects[NIGHT]
        assert seen["Mystery"]["how"] == "tonight" and seen["Mystery"]["revisionId"] == rid["IC 1848 (Soul Nebula)"]
        assert client.get("/api/ingest").json()["assignments"][NIGHT]["all"] == rid["IC 1848 (Soul Nebula)"]

        # One object to a finished revision: refused until confirmed, then honoured (it beats "all").
        finished = rid["M 31 (Andromeda Galaxy) R2"]
        assert lib.revisions[finished]["stage"]["value"] == "finished"
        body = {"revisionId": finished, "object": "Mystery"}
        assert client.post("/api/ingest/tonight", json=body).status_code == 409
        assert client.post("/api/ingest/tonight", json={**body, "confirm": True}).status_code == 200
        moves = {Path(a.src).name: a for a in svc.plan().actions if a.kind == "LIGHT"}
        assert moves["x0.fits"].revision_id == finished
        assert moves["s0.fits"].revision_id == rid["IC 1848 (Soul Nebula)"]

        # Clearing both falls back to the OBJECT mapping.
        client.post("/api/ingest/tonight", json={"revisionId": None, "object": "Mystery"})
        client.post("/api/ingest/tonight", json={"revisionId": None})
        assert NIGHT not in client.get("/api/ingest").json()["assignments"]
        assert {Path(h.src).name: h.reason for h in svc.plan().holds}["x0.fits"] == "unmapped"


def test_frames_without_a_night_folder_are_grouped_by_their_header_night(data_root, tmp_path):
    # NINA writing .Download\<object>\LIGHT\<filter>\ (no date folder), as on 2026-10-14.
    dl = data_root / ".Download" / "IC 405" / "LIGHT"
    for i, filt in enumerate(["Ha", "OIII", "SII", "Ha"]):
        f = frame(dl / filt / f"n{i}.fits", **light(FILTER=filt, OBJECT="IC 405", DATE_LOC=f"{NIGHT}T23:3{i}:00"))
        os.utime(f, (OLD + i, OLD + i))  # n3 is the newest
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        svc = app.state.engine.ingest
        rid = {r["folder"]: r["id"] for r in app.state.engine.library.revisions.values()}
        plan = svc.plan()
        assert plan.nights[NIGHT]["folder"] is False and plan.nights[NIGHT]["lights"] == 4
        assert {h.reason for h in plan.holds if "n" in Path(h.src).stem} == {"unmapped"}  # seen now, but no target

        client.post("/api/ingest/tonight", json={"revisionId": rid["IC 1848 (Soul Nebula)"]})
        plan = svc.plan()
        moves = {Path(a.src).name: a for a in plan.actions}
        assert set(moves) == {"n0.fits", "n1.fits", "n2.fits"}
        assert moves["n1.fits"].dst.replace("\\", "/").endswith(f"IC 1848 (Soul Nebula)/.SessionData/NIGHT_{NIGHT}/LIGHT/O/n1.fits")
        assert {Path(h.src).name: h.reason for h in plan.holds}["n3.fits"] == "newest"

        client.post("/api/ingest/mode", json={"mode": "auto"})
        client.post("/api/ingest/run")
        assert not (dl / "Ha" / "n0.fits").exists() and (dl / "Ha" / "n3.fits").exists()
        assert client.post("/api/ingest/undo", json={"night": NIGHT}).json()["movedBack"] == 3
        assert (dl / "OIII" / "n1.fits").exists()


def test_following_nina_routes_each_target_and_reopens_finished_only_when_allowed(data_root, tmp_path):
    import json

    for folder in ("M 31 (Andromeda Galaxy)", "M 31 (Andromeda Galaxy) R2"):
        rev = 2 if folder.endswith("R2") else 1
        (data_root / folder / "target.json").write_text(json.dumps(
            {"catalog_id": "M 31", "name": "Andromeda Galaxy", "revision": rev, "status": "finished", "notes": "keep me"}), encoding="utf-8")
    dl = data_root / ".Download"
    # Two targets in one night, NINA writing without a date folder; "IC1848" spelled without the space.
    for i in range(2):
        frame(dl / "IC1848" / "LIGHT" / "Ha" / f"a{i}.fits", **light(FILTER="Ha", OBJECT="IC1848", DATE_LOC=f"{NIGHT}T21:0{i}:00"))
        frame(dl / "M 31" / "LIGHT" / "L" / f"b{i}.fits", **light(FILTER="L", OBJECT="M 31", DATE_LOC=f"{NIGHT}T23:0{i}:00"))
    os.utime(dl / "M 31" / "LIGHT" / "L" / "b1.fits", (OLD + 99, OLD + 99))  # the newest light stays
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        svc = app.state.engine.ingest
        assert client.get("/api/ingest").json()["follow"] is True  # on by default

        plan = svc.plan()
        moves = {Path(a.src).name: a for a in plan.actions}
        assert "IC 1848" in moves["a0.fits"].target and "a1.fits" in moves
        held = {Path(h.src).name: h for h in plan.holds}
        assert held["b0.fits"].reason == "unmapped" and "finished" in held["b0.fits"].message and not plan.reopen

        # The toggle on: M 31 frames go to the newest finished revision, which is reopened first (Auto only).
        assert client.post("/api/ingest/follow", json={"finished": True}).json()["followFinished"] is True
        plan = svc.plan()
        assert [r["target"] for r in plan.reopen] == ["M 31 (Andromeda Galaxy) R2"]
        client.post("/api/ingest/mode", json={"mode": "auto"})
        client.post("/api/ingest/run")
        tj = json.loads((data_root / "M 31 (Andromeda Galaxy) R2" / "target.json").read_text(encoding="utf-8"))
        assert tj == {"catalog_id": "M 31", "name": "Andromeda Galaxy", "revision": 2, "status": "acquiring", "notes": "keep me"}
        assert (data_root / "M 31 (Andromeda Galaxy) R2" / ".SessionData" / f"NIGHT_{NIGHT}" / "LIGHT" / "L" / "b0.fits").exists()
        assert (dl / "M 31" / "LIGHT" / "L" / "b1.fits").exists()
        assert json.loads((data_root / "M 31 (Andromeda Galaxy)" / "target.json").read_text(encoding="utf-8"))["status"] == "finished"
        log = client.get("/api/ingest").json()["reopened"]
        assert [(r["folder"], r["from"], r["to"]) for r in log] == [("M 31 (Andromeda Galaxy) R2", "finished", "acquiring")]

        # Mark it finished again.
        app.state.engine._run_scan()
        rid = next(r["id"] for r in app.state.engine.library.revisions.values() if r["folder"] == "M 31 (Andromeda Galaxy) R2")
        assert client.post("/api/ingest/status", json={"revisionId": rid, "status": "finished"}).json()["previous"] == "acquiring"
        assert json.loads((data_root / "M 31 (Andromeda Galaxy) R2" / "target.json").read_text(encoding="utf-8"))["status"] == "finished"


def test_set_status_only_touches_a_target_folders_own_target_json(data_root):
    with pytest.raises(fileops.UnsafeOperation):
        fileops.set_status(data_root, ".Download", "acquiring")
    with pytest.raises(fileops.UnsafeOperation):
        fileops.set_status(data_root, "IC 1848 (Soul Nebula)/.SessionData", "acquiring")
    with pytest.raises(fileops.UnsafeOperation):
        fileops.set_status(data_root, "..", "acquiring")
    with pytest.raises(fileops.UnsafeOperation):
        fileops.set_status(data_root, "IC 1848 (Soul Nebula)", "deleted")
    assert fileops.set_status(data_root, "IC 1848 (Soul Nebula)", "finished", {"catalog_id": "IC 1848", "name": "Soul Nebula", "revision": 1, "x": 1}) is None
    import json
    assert json.loads((data_root / "IC 1848 (Soul Nebula)" / "target.json").read_text(encoding="utf-8")) == {
        "catalog_id": "IC 1848", "name": "Soul Nebula", "revision": 1, "status": "finished"}
    assert not list((data_root / "IC 1848 (Soul Nebula)").glob("*.part"))


def test_copies_resynced_into_download_are_recognised_and_removed_only_when_identical(data_root, tmp_path):
    import shutil

    dl = data_root / ".Download" / "Soul Nebula" / "LIGHT" / "Ha"
    for i in range(4):
        f = frame(dl / f"r{i}.fits", **light(FILTER="Ha", OBJECT="Soul Nebula", DATE_LOC=f"{NIGHT}T22:1{i}:00"))
        os.utime(f, (OLD + i, OLD + i))  # r3 is the newest and stays
    app = app_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        svc = app.state.engine.ingest
        client.post("/api/ingest/mode", json={"mode": "auto"})
        client.post("/api/ingest/run")
        moved = {Path(r["dst"]).name: r["dst"] for r in client.get("/api/ingest").json()["recent"]}
        assert set(moved) == {"r0.fits", "r1.fits", "r2.fits"}

        # robocopy re-syncs the observatory PC's folder: the moved frames come back, timestamps kept.
        for name, dst in moved.items():
            shutil.copy2(dst, dl / name)
        # A look-alike: same size and timestamp, different bytes. Never removed.
        fake = dl / "r2.fits"
        data = bytearray(fake.read_bytes())
        data[-1] ^= 0xFF
        st = fake.stat()
        fake.write_bytes(bytes(data))
        os.utime(fake, (st.st_atime, st.st_mtime))

        plan = svc.plan()
        reasons = {Path(h.src).name: h.reason for h in plan.holds}
        assert reasons == {"r0.fits": "ingested", "r1.fits": "ingested", "r2.fits": "ingested", "r3.fits": "newest"}
        assert plan.nights[NIGHT]["ingested"] == 3 and not plan.actions

        assert client.post("/api/ingest/remove-copies", json={"night": NIGHT}).status_code == 422
        r = client.post("/api/ingest/remove-copies", json={"night": NIGHT, "confirm": True}).json()
        assert r["removed"] == 2 and [Path(k["src"]).name for k in r["kept"]] == ["r2.fits"]
        assert not (dl / "r0.fits").exists() and not (dl / "r1.fits").exists()
        assert (dl / "r2.fits").exists() and (dl / "r3.fits").exists()
        assert all(Path(d).exists() for d in moved.values())  # the ingested frames are untouched
        # Undo still only moves back what was moved; the removed copies do not confuse it.
        assert client.post("/api/ingest/undo", json={"night": NIGHT}).json()["movedBack"] == 2
