"""Center on it: wait for the frame, stop, rewrite the target, slew + centre, guiding back, start again — and the
sequence left stopped when something fails. Against a fake NINA (no network)."""

import asyncio
import types

import pytest

from astrodash.library import _sexagesimal
from astrodash.observatory import center as center_mod
from astrodash.observatory.center import CenterJob, CenterRefused
from astrodash.observatory.guiding import Disturbances
from astrodash.observatory.nina import NinaError, target_containers

IC405 = {"object": "IC 405", "ra": 78.93698, "dec": 33.86023, "rotation": 267.78, "panel": None}


def _container(name, ra, dec, rot, status="CREATED"):
    h, d = ra / 15, abs(dec)
    return {"Name": f"{name}_Container", "Status": status, "Items": [],
            "Target": {"TargetName": name, "PositionAngle": rot, "InputCoordinates": {
                "RAHours": int(h), "RAMinutes": int(h * 60) % 60, "RASeconds": (h * 3600) % 60,
                "NegativeDec": dec < 0, "DecDegrees": int(d), "DecMinutes": int(d * 60) % 60, "DecSeconds": (d * 3600) % 60}}}


class FakeNina:
    def __init__(self, targets, running=True, slew_error=None, index_offset=0):
        self.targets = targets  # [[name, ra, dec, rot]]
        self.running = running
        self.slew_error = slew_error
        self.index_offset = index_offset  # NINA's index designating another container than ours
        self.calls = []

    async def get(self, path, timeout=None, **_kw):
        assert path == "/sequence/json"
        st = "RUNNING" if self.running else "CREATED"
        return [{"Name": "Targets", "Status": st, "Items": [
            {"Name": t[0], "Status": st, "Items": [{"Name": "Take Exposure", "Status": st}]} for t in self.targets]}]

    async def sequence_state(self):
        st = "RUNNING" if self.running else "CREATED"
        return [{"Name": "Targets_Container", "Status": st, "Items": [_container(*t, status=st) for t in self.targets]}]

    async def sequence_stop(self):
        self.calls.append("stop")
        self.running = False

    async def sequence_start(self):
        self.calls.append("start")
        self.running = True

    async def set_target(self, index, name, ra, dec, rotation):
        self.calls.append(("set", index, name, round(ra, 4), round(dec, 4), rotation))
        i = (index + self.index_offset) % len(self.targets)
        self.targets[i] = [name, ra, dec, rotation]

    async def slew_center(self, ra, dec):
        self.calls.append(("slew", round(ra, 4), round(dec, 4)))
        if self.slew_error:
            raise NinaError(self.slew_error)

    async def guider_start(self):
        self.calls.append("guide")


class FakeObs:
    def __init__(self, nina, seq_target="IC 405", guiding=True, exposing=False):
        self.nina = nina
        self.nina_status = {"connected": True}
        self.equipment = {"mount": {"Connected": True, "AtPark": False},
                          "camera": {"IsExposing": exposing, "ExposureEndTime": None},
                          "guider": {"State": "Guiding" if guiding else "Stopped"}}
        self.seq_target = seq_target
        self.image_saves = 0
        self.phd2 = None
        self.dist = Disturbances()
        self.refreshes = 0

    @property
    def sequence(self):
        return {"available": True, "status": "running" if self.nina.running else "idle", "target": self.seq_target}

    async def refresh_sequence(self):
        self.refreshes += 1

    def _settings(self):
        return {}


async def _yield(_s):
    await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(center_mod, "SETTLED_S", 0.0)


def _run(job, target=IC405, wait_frame=True, mode="center"):
    async def go():
        job.start(dict(target), wait_frame=wait_frame, mode=mode)
        await job.task
        return job.as_dict()

    return asyncio.run(go())


def test_running_waits_for_the_frame_then_stops_centres_and_starts_again():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    obs = FakeObs(nina, exposing=True)
    job = CenterJob(obs, sleep=_yield)
    polls = {"n": 0}

    async def sleep(_s):
        polls["n"] += 1
        if polls["n"] == 3:
            assert nina.calls == []  # nothing touched while the frame is exposing
            obs.image_saves += 1  # NINA saved it
        await asyncio.sleep(0)

    job._sleep = sleep
    st = _run(job)
    assert st["phase"] == "done", st
    assert nina.calls == ["stop", ("set", 0, "IC 405", 78.937, 33.8602, 267.78), ("slew", 78.937, 33.8602), "start"]
    assert not st["sequenceStopped"]


def test_update_only_rewrites_the_target():
    nina = FakeNina([["M 31", 10.68, 41.27, 0.0], ["IC 405", 78.9, 33.8, 267.78]], running=False)
    st = _run(CenterJob(FakeObs(nina), sleep=_yield), mode="update")
    assert st["phase"] == "done" and "now targets" in st["message"]
    assert nina.calls == [("set", 1, "IC 405", 78.937, 33.8602, 267.78)]  # name and rotation kept


def test_update_while_running_does_not_stop_or_slew():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    st = _run(CenterJob(FakeObs(nina, exposing=True), sleep=_yield), mode="update")
    assert st["phase"] == "done" and nina.running
    assert nina.calls == [("set", 0, "IC 405", 78.937, 33.8602, 267.78)]


def test_center_now_without_a_running_sequence_slews_only():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]], running=False)
    st = _run(CenterJob(FakeObs(nina), sleep=_yield))
    assert st["phase"] == "done" and "not changed" in st["message"]
    assert nina.calls == [("slew", 78.937, 33.8602)]
    obs = FakeObs(FakeNina([["IC 405", 78.9, 33.8, 267.78]], running=False))
    obs.equipment["mount"]["AtPark"] = True
    with pytest.raises(CenterRefused):
        CenterJob(obs, sleep=_yield).start(dict(IC405))


def test_an_older_name_of_the_same_target_is_accepted_and_ninas_name_kept():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    old = {**IC405, "object": "Flaming Star Nebula", "names": ["Flaming Star Nebula", "IC 405"]}
    st = _run(CenterJob(FakeObs(nina), sleep=_yield), target=old)
    assert st["phase"] == "done", st
    assert ("set", 0, "IC 405", 78.937, 33.8602, 267.78) in nina.calls and nina.calls[-1] == "start"


def test_refused_when_nina_images_another_target():
    nina = FakeNina([["M 31", 10.68, 41.27, 0.0]])
    job = CenterJob(FakeObs(nina, seq_target="M 31"), sleep=_yield)
    with pytest.raises(CenterRefused, match="M 31"):
        job.start(dict(IC405))
    assert nina.calls == []


def test_failed_centring_leaves_the_sequence_stopped():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]], slew_error="/equipment/mount/slew: Slew failed")
    st = _run(CenterJob(FakeObs(nina), sleep=_yield))
    assert st["phase"] == "failed" and "left stopped" in st["message"]
    assert "start" not in nina.calls and st["sequenceStopped"]


def test_guiding_that_does_not_come_back_is_asked_for_then_resumes():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    obs = FakeObs(nina)
    job = CenterJob(obs, sleep=_yield)

    async def slew(ra, dec):  # NINA's Center stopped guiding and did not restart it
        nina.calls.append("slew")
        obs.equipment["guider"]["State"] = "Stopped"

    async def guide():
        nina.calls.append("guide")
        obs.equipment["guider"]["State"] = "Guiding"

    nina.slew_center, nina.guider_start = slew, guide
    center_mod.GUIDE_RESTART_S, saved = 0.0, center_mod.GUIDE_RESTART_S
    try:
        st = _run(job)
    finally:
        center_mod.GUIDE_RESTART_S = saved
    assert st["phase"] == "done"
    assert nina.calls[-3:] == ["slew", "guide", "start"]


def test_a_wrong_container_is_put_back():
    nina = FakeNina([["M 31", 10.68, 41.27, 0.0], ["IC 405", 78.9, 33.8, 267.78]], running=False, index_offset=1)
    st = _run(CenterJob(FakeObs(nina), sleep=_yield), mode="update")
    assert st["phase"] == "failed" and "put back" in st["message"]
    assert [round(x, 2) for x in nina.targets[0][1:3]] == [10.68, 41.27] and nina.targets[0][0] == "M 31"


def test_cancel_while_waiting_for_the_frame_touches_nothing():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    obs = FakeObs(nina, exposing=True)
    job = CenterJob(obs, sleep=_yield)

    async def sleep(_s):
        job.cancel()
        obs.image_saves += 1
        await asyncio.sleep(0)

    job._sleep = sleep
    st = _run(job)
    assert st["phase"] == "cancelled" and nina.calls == []


def test_target_containers_and_sexagesimal_headers():
    cs = target_containers([{"Items": [_container("IC 405", 78.93698, -33.86023, 267.78)]}])
    assert cs[0]["name"] == "IC 405" and abs(cs[0]["ra"] - 78.93698) < 1e-6 and abs(cs[0]["dec"] + 33.86023) < 1e-6
    assert abs(_sexagesimal("05 15 45") - 5.2625) < 1e-9
    assert abs(_sexagesimal("+33 51 37") - (33 + 51 / 60 + 37 / 3600)) < 1e-9
    assert abs(_sexagesimal("-00 30 00") + 0.5) < 1e-9
    assert _sexagesimal("") is None and _sexagesimal(12.5) == 12.5


def test_session_targets_keep_the_first_target_of_the_night():
    from datetime import datetime, timezone

    from astrodash.library import _session_targets

    def light(t, ra, dec, status="ok"):
        return types.SimpleNamespace(status=status, location=None, time_utc=datetime(2026, 11, 16, t, tzinfo=timezone.utc),
                                     time_local=None, header={"OBJCTRA": ra, "OBJCTDEC": dec, "OBJCTROT": 267.78, "OBJECT": "IC 405"})

    out = _session_targets([light(3, "05 15 45", "+33 51 37"), light(1, "05 16 03", "+33 47 44"), light(2, "05 16 03", "+33 47 44", "copy")])
    assert len(out) == 1
    t = out[0]
    assert t["object"] == "IC 405" and t["variants"] == 2 and t["frames"] == 1
    assert abs(t["ra"] - (5 + 16 / 60 + 3 / 3600) * 15) < 1e-4  # the earliest, not the most frequent


def test_refused_during_a_meridian_flip_and_waits_for_an_autofocus_after_the_frame():
    nina = FakeNina([["IC 405", 78.9, 33.8, 267.78]])
    obs = FakeObs(nina)
    obs.dist.begin("meridian flip")
    with pytest.raises(CenterRefused, match="meridian flip"):
        CenterJob(obs, sleep=_yield).start(dict(IC405))
    obs.dist.end("meridian flip")

    obs.equipment["camera"]["IsExposing"] = True
    job = CenterJob(obs, sleep=_yield)
    seen = []

    async def sleep(_s):
        if obs.image_saves == 0:
            obs.image_saves += 1
            obs.dist.begin("autofocus")  # the AF After Time trigger starts right after the frame
        elif job.state["message"].startswith("Waiting for NINA's autofocus"):
            seen.append(list(nina.calls))
            obs.dist.end("autofocus")
        await asyncio.sleep(0)

    job._sleep = sleep
    st = _run(job)
    assert st["phase"] == "done" and seen == [[]]  # nothing was touched during the autofocus
    assert nina.calls[0] == "stop"


def test_slew_center_judges_by_error_not_success():
    """Plugin 2.2.15 answers Success=false for a centre that worked (Execute never sets the item's Status)."""
    import httpx

    from astrodash.observatory.nina import NinaRest

    answers = {
        "ok": {"Response": "Slew failed", "Error": "", "StatusCode": 200, "Success": False, "Type": "API"},
        "fail": {"Response": None, "Error": "Unknown error", "StatusCode": 500, "Success": False, "Type": "API"},
        "parked": {"Response": None, "Error": "Mount parked", "StatusCode": 409, "Success": False, "Type": "API"},
    }

    async def go(kind):
        rest = NinaRest("http://nina.test")
        rest._client = httpx.AsyncClient(base_url=rest.base, transport=httpx.MockTransport(
            lambda req: httpx.Response(200 if kind == "ok" else answers[kind]["StatusCode"], json=answers[kind])))
        try:
            await rest.slew_center(78.9, 33.8)
        finally:
            await rest.aclose()

    asyncio.run(go("ok"))
    for kind in ("fail", "parked"):
        with pytest.raises(NinaError):
            asyncio.run(go(kind))


def test_center_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from astrodash.api import create_app
    from astrodash.config import Config

    from .conftest import light, write_fits

    t = tmp_path / "Astro" / "IC 405 (Flaming Star Nebula)" / ".SessionData"
    write_fits(t / "NIGHT_2025-10-16" / "LIGHT" / "S" / "a.fits", **light(FILTER="S", OBJECT="Flaming Star Nebula", OBJCTRA="05 16 03",
               OBJCTDEC="+33 47 44", OBJCTROT=89.74, DATE_LOC="2025-10-16T22:00:00", DATE_OBS="2025-10-17T02:00:00"))
    write_fits(t / "NIGHT_2026-11-16" / "LIGHT" / "S" / "b.fits", **light(FILTER="S", OBJECT="IC 405", OBJCTRA="05 15 45",
               OBJCTDEC="+33 51 37", OBJCTROT=267.78, DATE_LOC="2026-11-16T22:00:00", DATE_OBS="2026-11-17T03:00:00"))
    write_fits(t / "NIGHT_2026-11-17" / "LIGHT" / "S" / "c.fits", **light(FILTER="S", DATE_LOC="2026-11-17T22:00:00",
               DATE_OBS="2026-11-18T03:00:00"))
    app = create_app(Config(data_root=tmp_path / "Astro", db_path=tmp_path / "var" / "c.db"), scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        rev = client.get("/api/revisions/ic-405-flaming-star-nebula").json()
        by = {s["folder"]: s.get("targets") for s in rev["sessions"]}
        assert by["NIGHT_2026-11-17"] == []
        assert by["NIGHT_2025-10-16"][0]["object"] == "Flaming Star Nebula"
        assert abs(by["NIGHT_2025-10-16"][0]["ra"] - (5 + 16 / 60 + 3 / 3600) * 15) < 1e-4

        body = {"revision": "ic-405-flaming-star-nebula", "session": "NIGHT_2025-10-16"}
        assert client.post("/api/live/center", json={**body, "revision": "nope"}).status_code == 404
        assert client.post("/api/live/center", json={**body, "session": "NIGHT_1999-01-01"}).status_code == 404
        assert client.post("/api/live/center", json={**body, "session": "NIGHT_2026-11-17"}).status_code == 422
        r = client.post("/api/live/center", json=body)  # the observatory is not running in tests
        assert r.status_code == 409 and "not reachable" in r.json()["detail"]

        obs = app.state.engine.observatory
        got = {}
        obs.center.start = lambda target, wait_frame=True, mode="center": got.update(target, mode=mode) or {"phase": "updating"}
        assert client.post("/api/live/center", json=body).json() == {"center": {"phase": "updating"}}
        assert got["names"] == ["Flaming Star Nebula", "IC 405"] and got["session"] == "NIGHT_2025-10-16" and got["mode"] == "center"
        client.post("/api/live/center", json={**body, "mode": "update"})
        assert got["mode"] == "update"
        assert client.post("/api/live/center", json={**body, "mode": "slew"}).status_code == 422
        assert client.get("/api/live/center").json() == {"center": None}
