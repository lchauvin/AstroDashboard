"""Organizer (PLAN §2.1, §3.2) on the synthetic data root. Never touches G:\\."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from astrodash.api import create_app
from astrodash.config import Config
from astrodash.organizer import apply as applier

from .conftest import light, write_fits


def files(root: Path) -> dict[str, int]:
    """Every file below the root (except the dashboard journal) → size: nothing may disappear."""
    out = {}
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            rel = p.relative_to(root).as_posix()
            if not rel.startswith(".Dashboard/"):
                out[rel] = p.stat().st_size
    return out


def client_for(data_root: Path, tmp_path: Path):
    app = create_app(Config(data_root=data_root, db_path=tmp_path / "var" / "org.db"), scan_on_start=False)
    return app


def lights_total(client) -> dict[str, int]:
    return {t["catalogId"] + str(r["revision"]): r["totals"]["lights"] for t in client.get("/api/targets").json()["targets"] for r in t["revisions"]}


def test_lint_and_dry_run_change_nothing(data_root, tmp_path):
    before = files(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        org = client.get("/api/organizer").json()
        m31 = next(r for r in org["revisions"] if r["revisionId"] == "m-31-andromeda-galaxy")
        codes = {l["code"] for l in m31["lint"]}
        assert {"year", "panel", "flatwizard"} <= codes and not m31["canonical"]
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        assert plan["status"] == "draft" and plan["counts"]["move"] >= 3
    assert files(data_root) == before


def test_apply_migrates_m31_without_losing_a_frame_and_undo_restores_it(data_root, tmp_path):
    before = files(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        totals = lights_total(client)
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        pid = plan["id"]
        assert client.post(f"/api/organizer/plans/{pid}/apply", json={"confirm": "yes"}).status_code == 422  # typed approval
        done = client.post(f"/api/organizer/plans/{pid}/apply", json={"confirm": pid}).json()
        assert done["status"] == "applied", done["result"]
        after = files(data_root)
        assert sorted(after.values()) == sorted(before.values()) and len(after) == len(before)
        m = data_root / "M 31 (Andromeda Galaxy)"
        assert (m / ".SessionData" / "NIGHT_2026-08-20" / "PANEL_1" / "LIGHT" / "L" / "l1.fits").is_file()
        assert (m / ".SessionData" / "NIGHT_2026-08-20" / "PANEL_2" / "LIGHT" / "L" / "l2.fits").is_file()
        assert (m / ".SessionData" / "NIGHT_2026-08-20" / "FLAT" / "L" / "f.fits").is_file()
        assert (m / ".MispointedFrames_Quarantine" / "NIGHT_2026-08-20" / "PANEL_1" / "LIGHT" / "L" / "q.fits").is_file()
        assert not (m / "2026").exists()
        journal = (data_root / ".Dashboard" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(journal) == len(plan["ops"])
        # The Library reads the new layout with the same totals.
        app.state.engine._run_scan()
        assert lights_total(client) == totals
        assert next(r for r in client.get("/api/organizer").json()["revisions"] if r["revisionId"] == "m-31-andromeda-galaxy")["canonical"]

        # Undo is a new plan that needs its own approval, and restores the exact tree.
        undo = client.post(f"/api/organizer/plans/{pid}/undo").json()
        assert undo["status"] == "draft" and files(data_root) == after
        client.post(f"/api/organizer/plans/{undo['id']}/apply", json={"confirm": undo["id"]})
        assert files(data_root) == before
        assert client.post(f"/api/organizer/plans/{pid}/apply", json={"confirm": pid}).status_code == 409  # only drafts apply


def test_stored_diagnostics_follow_the_frames(data_root, tmp_path):
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        old = "M 31 (Andromeda Galaxy)/2026/.SessionData/NIGHT_2026-08-20/M 31 Panel 1/LIGHT/L/l1.fits"
        app.state.engine.cache.save_diagnostic("m-31-andromeda-galaxy", "NIGHT_2026-08-20", 1.0, {"lights": []},
                                               {"lights": [{"frames": [{"path": old}]}]})
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]})
        stored = app.state.engine.cache.diagnostic("m-31-andromeda-galaxy", "NIGHT_2026-08-20")
        new = stored["result"]["lights"][0]["frames"][0]["path"]
        assert new == "M 31 (Andromeda Galaxy)/.SessionData/NIGHT_2026-08-20/PANEL_1/LIGHT/L/l1.fits"
        assert (data_root / new).is_file()


def test_session_rename_moves_its_diagnostics(data_root, tmp_path):
    t = data_root / "NGC 1 (Test)" / ".SessionData"
    write_fits(t / "2026-01-05" / "LIGHT" / "H" / "a.fits", **light(FILTER="H", DATE_LOC="2026-01-05T22:00:00"))
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        app.state.engine.cache.save_diagnostic("ngc-1-test", "2026-01-05", 1.0, {"lights": []},
                                               {"lights": [{"frames": [{"path": "NGC 1 (Test)/.SessionData/2026-01-05/LIGHT/H/a.fits"}]}]})
        plan = client.post("/api/organizer/plans", json={"revisionId": "ngc-1-test"}).json()
        client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]})
        assert app.state.engine.cache.diagnostic("ngc-1-test", "2026-01-05") is None
        moved = app.state.engine.cache.diagnostic("ngc-1-test", "NIGHT_2026-01-05")
        assert moved["result"]["lights"][0]["frames"][0]["path"] == "NGC 1 (Test)/.SessionData/NIGHT_2026-01-05/LIGHT/H/a.fits"


def test_flagged_session_goes_to_trash_not_away(data_root, tmp_path):
    before = files(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/plans", json={"revisionId": "ic-1848-soul-nebula"}).json()
        trash = [o for o in plan["ops"] if o["op"] == "trash"]
        assert trash and trash[0]["dst"].startswith(f".Trash/{plan['id']}/IC 1848 (Soul Nebula)/.SessionData/[GARBAGE]")
        client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]})
        after = files(data_root)
        assert len(after) == len(before)  # moved, not deleted
        assert any(k.startswith(f".Trash/{plan['id']}/") for k in after)
        t = client.get("/api/organizer/trash").json()
        assert t["files"] >= 1 and t["plans"][0]["plan"] == plan["id"]


def test_conflicts_block_and_changed_sources_stop(data_root, tmp_path):
    m = data_root / "M 31 (Andromeda Galaxy)"
    # A clash: the flattened .SessionData would land on an existing file of the same name
    write_fits(m / ".SessionData" / "NIGHT_2026-08-20" / "FLAT" / "L" / "f.fits", **light(FILTER="L"))
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        assert plan["status"] == "blocked" and plan["conflicts"]
        assert client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]}).status_code == 409

    # A source changed after the plan was made: the plan stops before touching it.
    (m / ".SessionData" / "NIGHT_2026-08-20" / "FLAT" / "L" / "f.fits").unlink()  # test fixture cleanup only
    app = client_for(data_root, tmp_path / "b")
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/plans", json={"revisionId": "ic-1848-soul-nebula"}).json()
        target = next(o for o in plan["ops"] if o["op"] == "trash")
        f = next(Path(data_root, target["src"]).rglob("*.fits"))
        with open(f, "ab") as fh:  # simulate NINA/WBPP touching it in between (fixture only)
            fh.write(b"x" * 2880)
        # dir-level trash has no size check, so add a file-level move to see the mtime/size guard
        res = applier.apply_plan(data_root, "p1", [{"op": "move", "src": f.relative_to(data_root).as_posix(), "dst": "IC 1848 (Soul Nebula)/.SessionData/NIGHT_2026-02-01/x.fits", "kind": "file", "size": 1, "mtime": 0}])
        assert res["error"] and "changed" in res["error"] and f.is_file()


@pytest.mark.parametrize("op", [
    {"op": "move", "src": "IC 1848 (Soul Nebula)/.SessionData", "dst": ".Download/x"},
    {"op": "move", "src": "IC 1848 (Soul Nebula)/.SessionData", "dst": ".Calibration/x"},
    {"op": "move", "src": "IC 1848 (Soul Nebula)/.SessionData", "dst": "../outside"},
    {"op": "trash", "src": "IC 1848 (Soul Nebula)/.SessionData", "dst": ".Trash/other-plan/x"},
    {"op": "rmdir_empty", "dst": "IC 1848 (Soul Nebula)"},
    {"op": "write_new", "dst": "IC 1848 (Soul Nebula)/.SessionData/NIGHT_2026-02-01/LIGHT/H/h0.fits", "text": "x"},
    {"op": "delete", "dst": "IC 1848 (Soul Nebula)"},
    {"op": "repoint", "src": "IC 1848 (Soul Nebula)/.SessionData/NIGHT_2026-02-01/LIGHT/H/h0.fits", "dst": ".Dashboard/backups/p1/x", "size": 0, "mtime": 0, "sha1": "", "refs": 0},
])
def test_apply_refuses_unsafe_operations(data_root, op):
    before = files(data_root)
    res = applier.apply_plan(data_root, "p1", [op])
    assert res["error"] and res["done"] == []
    assert files(data_root) == before


def test_new_revision_creates_folders_and_target_json_only(data_root, tmp_path):
    before = files(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/new-revision", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        assert plan["target"] == "M 31 (Andromeda Galaxy) R3"
        client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]})
        tj = data_root / "M 31 (Andromeda Galaxy) R3" / "target.json"
        assert '"revision": 3' in tj.read_text(encoding="utf-8")
        assert set(files(data_root)) - set(before) == {"M 31 (Andromeda Galaxy) R3/target.json"}


def test_only_the_organizer_api_imports_apply():
    pkg = Path(__file__).resolve().parents[1] / "astrodash"

    def imports_apply(text: str) -> bool:
        return any(line.lstrip().startswith(("from ", "import ")) and "organizer" in line and "apply" in line for line in text.splitlines())

    users = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py") if imports_apply(p.read_text(encoding="utf-8"))]
    assert users == ["api_organizer.py"]


def _project(data_root: Path) -> tuple[Path, str, bytes]:
    """A PixInsight project in the M 31 target referencing the year-level paths, an old root and unrelated files."""
    root = data_root.as_posix()
    night = "M 31 (Andromeda Galaxy)/2026/.SessionData/NIGHT_2026-08-20"
    url = f"file:///{root}/{night}/M 31 Panel 2/LIGHT/L/l2.fits".replace(" ", "%20")
    xosm = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<xosm><Project sealedProject="true"/>\n'
        f'<td id="path">{root}/{night}/M 31 Panel 1/LIGHT/L/l1.fits</td>\n'
        f'<property id="XISF:ResourceURL" type="0x100000000">{url}</property>\n'
        f'<td id="filePath">E:/Photo/Astro/{night}/FlatWizard/FLAT/L/f.fits</td>\n'
        '<td id="path">F:/Astrophoto/MARS Database/MARS-DR1.xmars</td>\n'
        '<td id="path">C:/astro/ALIGNED/a.xisf</td>\n</xosm>\n'
    ).encode("utf-8")
    bundle = data_root / "M 31 (Andromeda Galaxy)" / "M31.pxiproject"
    (bundle / "project.data").mkdir(parents=True)
    (bundle / "project.xosm").write_bytes(xosm)
    (bundle / "project.data" / "project.seal").write_bytes(b"seal")
    return bundle, root, xosm


def test_pixinsight_projects_follow_the_moves_and_undo_restores_them(data_root, tmp_path):
    bundle, root, original = _project(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        rp = [o for o in plan["ops"] if o["op"] == "repoint"]
        assert len(rp) == 1 and rp[0]["refs"] == 3 and plan["ops"][-1]["op"] == "repoint"
        assert rp[0]["dst"] == f".Dashboard/backups/{plan['id']}/M 31 (Andromeda Galaxy)/M31.pxiproject/project.xosm"
        assert any(l["code"] == "pxiproject" for l in plan["lint"]) and any("seal" in w for w in plan["warnings"])
        done = client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]}).json()
        assert done["status"] == "applied", done["result"]

        text = (bundle / "project.xosm").read_text(encoding="utf-8")
        night = "M 31 (Andromeda Galaxy)/.SessionData/NIGHT_2026-08-20"
        assert f"{root}/{night}/PANEL_1/LIGHT/L/l1.fits" in text
        assert f"file:///{root}/{night}/PANEL_2/LIGHT/L/l2.fits".replace(" ", "%20") in text
        assert f"{root}/{night}/FLAT/L/f.fits" in text            # E:/Photo/Astro → the data root, and flattened
        assert "F:/Astrophoto/MARS Database/MARS-DR1.xmars" in text and "C:/astro/ALIGNED/a.xisf" in text
        assert "/2026/" not in text and "E:/Photo" not in text
        for line in text.splitlines():                              # every re-pointed path exists
            if root in line:
                p = line.split(">", 1)[1].split("<", 1)[0].replace("file:///", "").replace("%20", " ")
                assert Path(p).is_file(), p
        assert (data_root / rp[0]["dst"]).read_bytes() == original  # the original is kept
        assert (bundle / "project.data" / "project.seal").read_bytes() == b"seal"
        assert not (bundle / "project.xosm.part").exists()

        undo = client.post(f"/api/organizer/plans/{plan['id']}/undo").json()
        assert [o["op"] for o in undo["ops"][:2]] == ["trash", "move"]
        assert client.post(f"/api/organizer/plans/{undo['id']}/apply", json={"confirm": undo["id"]}).json()["status"] == "applied"
        assert (bundle / "project.xosm").read_bytes() == original
        assert not (data_root / rp[0]["dst"]).exists()


def test_project_saved_after_planning_is_not_touched(data_root, tmp_path):
    bundle, _, _ = _project(data_root)
    app = client_for(data_root, tmp_path)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        plan = client.post("/api/organizer/plans", json={"revisionId": "m-31-andromeda-galaxy"}).json()
        saved = (bundle / "project.xosm").read_bytes() + b"<!-- saved in PixInsight -->\n"
        (bundle / "project.xosm").write_bytes(saved)  # fixture: the project is saved in between
        done = client.post(f"/api/organizer/plans/{plan['id']}/apply", json={"confirm": plan["id"]}).json()
        assert done["status"] == "partial" and "changed" in done["result"]["error"]
        assert (bundle / "project.xosm").read_bytes() == saved
        assert not (data_root / ".Dashboard" / "backups").exists()
