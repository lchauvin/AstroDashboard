"""The sky monitor (clouds from PHD2's guide star) and the ASCOM Alpaca safety monitor NINA reads it through."""

import os
import subprocess
import sys
from pathlib import Path
import socket
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from astrodash import skymonitor
from astrodash.observatory import alpaca
from astrodash.observatory.sky import DEFAULTS, SkyMonitor, horizon_at

ON = {**DEFAULTS, "enabled": True}


class _Store:
    def __init__(self, sites=None):
        self.data, self._sites = {}, sites

    def get_json(self, k):
        return self.data.get(k)

    def set_json(self, k, v):
        self.data[k] = v


class _SiteStore(_Store):
    def sites(self):
        return self._sites


def _guide(m, t0, t1, mass, dt=2.0, exposure_ms=2000):
    t = t0
    while t < t1:
        m.on_phd2_event({"Event": "GuideStep", "StarMass": mass}, t, exposure_ms)
        t += dt


def _lose(m, t0, t1, dt=1.0):
    t = t0
    while t < t1:
        m.on_phd2_event({"Event": "StarLost"}, t)
        t += dt


def test_a_lost_star_is_unsafe_and_steady_guiding_clears_it():
    m = SkyMonitor(_Store())
    m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, 0)
    _lose(m, 1, 60)
    m.tick(45, ON)
    assert m.safe  # 44 s of the last 60 lost
    m.tick(46, {**ON, "enabled": False})
    assert m.safe  # off by default
    m.tick(46, ON)
    assert not m.safe and m.log[-1]["message"] == "Unsafe: guide star lost 45 s of the last 60 s."
    assert m.as_dict(46, ON)["state"] == "cloudy" and m.as_dict(46, ON)["giveUpAt"] == 46 + 120 * 60

    m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, 100)
    m.on_phd2_event({"Event": "StarLost"}, 130)  # a passing cloud: the steady time starts again
    m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, 140)
    m.tick(259, ON)
    assert not m.safe and m.as_dict(259, ON)["steadyForS"] == 119
    m.tick(260, ON)
    assert m.safe and "steady for 120 s" in m.log[-1]["message"]


def test_broken_cloud_is_unsafe_but_short_blips_are_not():
    m = SkyMonitor(_Store())
    t = 0.0
    while t < 120:  # 8 s lost, 2 s guided, over and over: never a minute without a guide step
        _lose(m, t, t + 8, dt=2)
        m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, t + 8)
        t += 10
    assert m.lost_seconds(120, 60) == 48 and m.as_dict(120, ON)["lostSharePct"] == 80
    m.tick(120, {**ON, "lostPct": 90})
    assert m.safe
    m.tick(120, ON)
    assert not m.safe

    m = SkyMonitor(_Store())
    m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, 0)
    for t in range(0, 300, 10):  # a 3 s blip every 10 s: 30 %
        m.on_phd2_event({"Event": "StarLost"}, t + 5)
        m.on_phd2_event({"Event": "GuideStep", "StarMass": 5000}, t + 8)
    m.tick(300, ON)
    assert m.safe and m.as_dict(300, ON)["state"] == "watching"


def test_a_dimmed_star_is_unsafe_before_phd2_loses_it():
    m = SkyMonitor(_Store())
    _guide(m, 0, 100, 5000)
    assert m.baseline(100) is None  # under 2 min of clear guiding: not known yet
    _guide(m, 100, 300, 5000)
    m.tick(300, ON)
    assert m.baseline(300) == 5000 and m.as_dict(300, ON)["brightnessPct"] == 100
    _guide(m, 300, 330, 2000)  # haze: 40 %
    m.tick(330, ON)
    assert m.safe  # half the minute so far
    _guide(m, 330, 362, 2000)
    m.tick(362, ON)
    assert not m.safe and m.log[-1]["message"] == "Unsafe: guide star down to 40% of its clear-sky brightness."

    # PHD2 never lost the star, so guiding has been steady all along: the brightness decides.
    _guide(m, 362, 600, 3000)  # 60 %: above the 50 % that tripped it, under the 65 % that clears it
    m.tick(600, ON)
    assert not m.safe and m.as_dict(600, ON)["clearNeedPct"] == 65
    _guide(m, 600, 650, 4900)
    m.tick(650, ON)
    assert not m.safe  # the last 2 min are still mostly 60 %
    _guide(m, 650, 722, 4900)
    m.tick(722, ON)
    assert m.safe and "at 98% of its clear-sky brightness" in m.log[-1]["message"]


def test_the_clear_sky_brightness_holds_through_a_long_haze():
    m = SkyMonitor(_Store())
    _guide(m, 0, 300, 5000)
    for t in range(300, 2700, 60):  # 40 min of haze: longer than the 30 min the brightness is taken from
        _guide(m, t, t + 60, 1500)
        m.tick(t + 60, ON)
    assert not m.safe and m.baseline(2700) == 5000 and m.as_dict(2700, ON)["brightnessPct"] == 30


def test_a_new_guide_exposure_restarts_the_clear_sky_brightness():
    m = SkyMonitor(_Store())
    _guide(m, 0, 300, 5000, exposure_ms=2000)
    assert m.baseline(300) == 5000
    _guide(m, 300, 310, 10000, exposure_ms=4000)  # twice the exposure, twice the mass: not a brighter sky
    assert m.baseline(310) is None
    _guide(m, 310, 600, 10000, exposure_ms=4000)
    assert m.baseline(600) == 10000


def test_the_end_of_a_guiding_run_means_clear():
    m = SkyMonitor(_Store())
    _lose(m, 0, 60)
    m.tick(60, ON)
    assert not m.safe
    m.on_phd2_event({"Event": "GuidingStopped"}, 61)  # a flip, an autofocus, a slew, you
    assert m.safe and "Guiding stopped, so there is no more evidence" in m.log[-1]["message"]
    m.tick(62, ON)
    assert m.safe and m.as_dict(62, ON)["state"] == "idle" and m.lost_seconds(62, 60) == 0

    _lose(m, 100, 160)
    m.tick(160, ON)
    m.end_run("PHD2 disconnected", 161)
    assert m.safe and m.log[-1]["message"].startswith("Clear: PHD2 disconnected")


def test_an_obstruction_a_give_up_and_you_all_mean_clear():
    m = SkyMonitor(_Store())
    _lose(m, 0, 60)
    m.tick(60, ON)
    assert not m.safe
    m.tick(61, ON, obstructed="The mount points below the horizon of Home")
    assert m.safe and m.log[-1]["message"] == "Clear: The mount points below the horizon of Home, which is not cloud."
    assert m.as_dict(61, ON)["state"] == "obstructed"
    m.tick(62, ON)
    assert not m.safe  # out of the profile again, and the star still lost

    _lose(m, 62, 62 + 120 * 60, dt=10)
    m.tick(62 + 120 * 60, ON)
    assert m.safe and m.gave_up and "more likely an obstruction" in m.log[-1]["message"]
    m.tick(63 + 120 * 60, ON)
    assert m.safe and m.as_dict(63 + 120 * 60, ON)["state"] == "gaveUp"  # until the next guiding run

    m.on_phd2_event({"Event": "StarSelected"}, 10_000)
    _lose(m, 10_000, 10_060)
    m.tick(10_060, ON)
    assert not m.safe
    m.clear_now(10_061)
    assert m.safe and m.gave_up and "you said so" in m.log[-1]["message"]

    m.on_phd2_event({"Event": "StartCalibration"}, 20_000)
    _lose(m, 20_000, 20_060)
    m.tick(20_060, ON)
    assert not m.safe
    m.tick(20_061, {**ON, "enabled": False})
    assert m.safe and "turned off" in m.log[-1]["message"]
    assert m.store.data["sky_monitor"]["log"][-1] == m.log[-1]  # kept across restarts


def test_horizon_at_interpolates_round_the_circle():
    prof = [{"az": 90, "alt": 30}, {"az": 0, "alt": 10}, {"az": 270, "alt": 20}, {"az": 180, "alt": 10}]
    assert horizon_at(prof, 45) == 20
    assert horizon_at(prof, 315) == 15  # between 270° and 360° = 0°
    assert horizon_at(prof, 0) == 10 and horizon_at(prof, 360) == 10
    assert horizon_at([{"az": 100, "alt": 12}], 300) == 12
    assert horizon_at([], 10) is None


def test_the_runtime_reads_the_pointing_against_the_site_horizon(tmp_path):
    from astrodash.observatory import runtime

    sites = [{"name": "Away", "lat": 10.0, "lon": 10.0, "horizon": []},
             {"name": "Home", "lat": 45.5, "lon": -73.6, "isDefault": False, "horizon": [{"az": 0, "alt": 5}, {"az": 180, "alt": 35}]}]
    obs = runtime.Observatory(lambda: {"skyMonitor": {"enabled": True}}, _SiteStore(sites), lambda: tmp_path)
    obs.equipment = {"mount": {"Connected": True, "Altitude": 25.0, "Azimuth": 170.0, "SiteLatitude": 45.5, "SiteLongitude": -73.6}}
    assert obs._obstruction() == "The mount points below the horizon of Home (altitude 25° at azimuth 170°, horizon 33°)"
    obs.equipment["mount"]["SiteLatitude"] = None  # no coordinates, no default: the first site, without a profile
    assert obs._obstruction() is None
    obs.equipment["mount"]["SiteLatitude"] = 45.5
    obs.equipment["mount"]["Altitude"] = 40.0
    assert obs._obstruction() is None

    now = time.time()
    _lose(obs.sky, now - 60, now)
    obs.sky_tick(now)
    assert not obs.sky.safe and obs.dist.events[-1] == (now, "clouds")
    obs.equipment["mount"]["Altitude"] = 20.0
    obs.sky_tick(now + 1)
    assert obs.sky.safe and obs.dist.events[-1] == (now + 1, "clear")
    assert obs.snapshot()["sky"]["state"] == "obstructed"

    obs = runtime.Observatory(lambda: {}, _Store(), lambda: tmp_path)  # a store without sites
    obs.equipment = {"mount": {"Connected": True, "Altitude": 5.0, "Azimuth": 10.0}}
    assert obs._obstruction() is None


def test_alpaca_safety_monitor():
    m = SkyMonitor(_Store())
    c = TestClient(alpaca.create_app(m))
    r = c.get("/management/v1/configureddevices", params={"ClientTransactionID": 7}).json()
    assert r["Value"] == [{"DeviceName": "AstroDashboard sky monitor", "DeviceType": "SafetyMonitor", "DeviceNumber": 0,
                           "UniqueID": alpaca.UNIQUE_ID}]
    assert r["ClientTransactionID"] == 7 and r["ErrorNumber"] == 0
    assert c.get("/management/apiversions").json()["Value"] == [1]
    assert c.get("/management/v1/description").json()["Value"]["ServerName"] == "AstroDashboard"

    dev = "/api/v1/safetymonitor/0"
    assert c.get(f"{dev}/issafe").json()["Value"] is False  # not connected: never safe, as ASCOM asks
    r = c.put(f"{dev}/connected", data={"Connected": "True", "ClientID": "1", "ClientTransactionID": "3"}).json()
    assert r["ClientTransactionID"] == 3 and r["ErrorNumber"] == 0 and "Value" not in r
    assert c.get(f"{dev}/connected").json()["Value"] is True
    assert c.get(f"{dev}/IsSafe", params={"clientid": 1}).json()["Value"] is True and m.polled["client"]
    m.cloudy_since = 1.0
    assert c.get(f"{dev}/issafe").json()["Value"] is False
    state = {d["Name"]: d["Value"] for d in c.get(f"{dev}/devicestate").json()["Value"]}
    assert state["IsSafe"] is False and "TimeStamp" in state
    assert c.get(f"{dev}/interfaceversion").json()["Value"] == 3
    assert c.get(f"{dev}/name").json()["Value"] == alpaca.DEVICE_NAME
    assert c.get(f"{dev}/supportedactions").json()["Value"] == []

    assert c.put(f"{dev}/connected", data={"Connected": "maybe"}).status_code == 400
    assert c.get("/api/v1/safetymonitor/1/issafe").status_code == 400
    assert c.get(f"{dev}/bogus").status_code == 400
    assert c.put(f"{dev}/action", data={"Action": "x", "Parameters": ""}).json()["ErrorNumber"] == alpaca.NOT_IMPLEMENTED
    c.put(f"{dev}/disconnect", data={"ClientID": "1"})
    assert c.get(f"{dev}/connected").json()["Value"] is False
    c.put(f"{dev}/connect", data={"ClientID": "1"})
    assert c.get(f"{dev}/connected").json()["Value"] is True
    assert "Settings → Live · Ingest" in c.get("/setup").text
    assert c.get("/setup/v1/safetymonitor/0/setup").status_code == 200


def test_alpaca_discovery_answers_with_the_port():
    class Transport:
        def __init__(self):
            self.sent = []

        def sendto(self, data, addr):
            self.sent.append((data, addr))

    d, t = alpaca._Discovery(3008), Transport()
    d.connection_made(t)
    d.datagram_received(b"alpacadiscovery1", ("192.168.2.200", 50000))
    d.datagram_received(b"something else", ("192.168.2.200", 50000))
    assert t.sent == [(b'{"AlpacaPort": 3008}', ("192.168.2.200", 50000))]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_alpaca_connected_is_per_client_and_restored():
    m, heard = SkyMonitor(_Store()), []
    c = TestClient(alpaca.create_app(m, {"7"}, lambda *a: heard.append(a)))
    dev = "/api/v1/safetymonitor/0"
    # NINA (ClientID 7) was connected before the engine restarted: still connected, still safe
    assert c.get(f"{dev}/connected", params={"ClientID": 7}).json()["Value"] is True
    assert c.get(f"{dev}/issafe", params={"ClientID": 7}).json()["Value"] is True
    # another app connects and disconnects: NINA stays connected
    c.put(f"{dev}/connected", data={"Connected": "true", "ClientID": "9"})
    c.put(f"{dev}/connected", data={"Connected": "false", "ClientID": "9"})
    assert c.get(f"{dev}/connected", params={"ClientID": 9}).json()["Value"] is False
    assert c.get(f"{dev}/connected", params={"ClientID": 7}).json()["Value"] is True
    assert [(what, cid, clients) for what, cid, _, clients in heard] == [("connected", "9", {"7", "9"}),
                                                                         ("disconnected", "9", {"7"})]
    c.put(f"{dev}/disconnect", data={"ClientID": "7"})
    assert c.get(f"{dev}/issafe", params={"ClientID": 7}).json()["Value"] is False and heard[-1][3] == set()


def test_sky_monitor_keeps_the_alpaca_clients_over_a_restart():
    store = _Store()
    m = SkyMonitor(store)
    m.alpaca_clients = {"7"}
    m.note("x")
    assert SkyMonitor(store).alpaca_clients == {"7"}


def _wait_for(cond, seconds=30.0):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if cond():
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return False


def test_the_bridge_says_clear_without_an_engine_and_keeps_events_until_delivered():
    b = alpaca.Bridge(3008)
    assert b.safe  # no verdict yet: no evidence, clear
    assert "started" in b.events[0]["message"]
    report = b.report()
    b.answered({"safe": False, "clients": ["7"]}, len(report["events"]), now=time.time())
    assert not b.safe and b.events == [] and b.clients == {"7"}  # the engine's kept clients, taken on once
    b.on_connection("connected", "9", "192.168.2.200", {"7", "9"})
    b.failed(now=time.time())  # the engine missed one: the event waits for the next report
    assert b.events[-1]["clients"] == ["7", "9"] and not b.stale and not b.safe
    b.last_ok = time.time() - alpaca.STALE_S - 1  # silent too long: clear, and said so
    b.failed()
    assert b.safe and b.stale and "saying clear" in b.events[-1]["message"]
    b.answered({"safe": False, "clients": []}, 0)
    assert not b.safe and "answers again" in b.events[-1]["message"] and b.clients == {"7"}


def test_the_engine_takes_the_reports_and_says_when_they_stop():
    m = SkyMonitor(_Store())
    now = time.time()
    assert not m.server_state(now)["running"] and "has not reported" in m.server_state(now)["error"]
    m.cloudy_since = now
    r = m.report({"port": 3008, "discovery": True, "pid": 42, "polled": {"t": now, "client": "192.168.2.200"},
                  "events": [{"t": now, "message": "192.168.2.200 (ClientID 7) connected", "clients": ["7"]}]}, now)
    assert r == {"safe": False, "clients": ["7"]}
    assert m.server_state(now)["running"] and m.server_state(now)["pid"] == 42 and m.polled["client"] == "192.168.2.200"
    assert m.log[-1]["message"] == "Alpaca: 192.168.2.200 (ClientID 7) connected."
    assert m.store.data["sky_monitor"]["alpacaClients"] == ["7"]
    assert not m.server_state(now + 60)["running"]


class _Child:
    def __init__(self, polls_alive):
        self.polls_alive, self.returncode, self.killed = polls_alive, None, False

    def poll(self):
        if self.killed or self.polls_alive <= 0:
            self.returncode = self.returncode if self.returncode is not None else 1
            return self.returncode
        self.polls_alive -= 1
        return None

    def kill(self):
        self.killed, self.returncode = True, -9

    def wait(self):
        return self.returncode


def test_the_supervisor_restarts_a_server_that_exits_or_hangs():
    clock = [0.0]
    children = []

    def start(restarts):
        children.append((restarts, _Child(polls_alive=100)))
        return children[-1][1]

    def sleep(s):
        clock[0] += s

    sup = skymonitor.Supervisor(start, healthy=lambda: False, sleep=sleep, clock=lambda: clock[0])
    sup.run_once()  # hangs after its grace: killed after UNHEALTHY failed checks
    assert children[0][1].killed and sup.restarts == 1

    sup = skymonitor.Supervisor(lambda r: _Child(polls_alive=2), healthy=lambda: True, sleep=sleep, clock=lambda: clock[0])
    assert sup.run_once() == 2.0 and sup.run_once() == 4.0 and sup.restarts == 2  # crashes back off


@pytest.mark.skipif(sys.platform != "win32", reason="the mutex is Windows only")
def test_only_one_supervisor_per_port():
    port = _free_port()
    assert skymonitor.single_instance(port)
    assert not skymonitor.single_instance(port)


class _FakeEngine:
    """The engine's /api/live/sky/alpaca, in a thread: records reports, answers with a verdict."""

    def __init__(self, safe=False, clients=("7",)):
        import http.server
        import json
        import threading

        self.reports, self.safe, self.clients = [], safe, list(clients)
        engine = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                engine.reports.append(body)
                out = json.dumps({"safe": engine.safe, "clients": engine.clients}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/live/sky/alpaca"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def messages(self):
        return [e["message"] for r in list(self.reports) for e in r["events"]]

    def pids(self):
        return {r["pid"] for r in list(self.reports)}


def _run(args):
    return subprocess.Popen([sys.executable, "-m", "astrodash.skymonitor", *args], cwd=Path(skymonitor.__file__).parents[1],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_the_standalone_server_answers_nina_from_the_engines_verdict():
    engine, port = _FakeEngine(safe=False, clients=["7"]), _free_port()
    proc = _run(["--serve", "--port", str(port), "--host", "127.0.0.1", "--no-discovery", "--engine-url", engine.url])
    dev = f"http://127.0.0.1:{port}/api/v1/safetymonitor/0"
    try:
        assert _wait_for(lambda: skymonitor.answers("127.0.0.1", port, 1)) and _wait_for(lambda: len(engine.reports) > 1)
        # NINA (ClientID 7) was connected before this process started: the engine kept it
        assert httpx.get(f"{dev}/connected", params={"ClientID": 7}).json()["Value"] is True
        assert httpx.get(f"{dev}/issafe", params={"ClientID": 7}).json()["Value"] is False  # the engine says cloudy
        engine.safe = True
        assert _wait_for(lambda: httpx.get(f"{dev}/issafe", params={"ClientID": 7}).json()["Value"] is True, 5)
        httpx.put(f"{dev}/connected", data={"Connected": "true", "ClientID": "9"})
        assert _wait_for(lambda: any("(ClientID 9) connected" in m for m in engine.messages()), 5)
        assert any(r["polled"] for r in engine.reports)
    finally:
        proc.kill()
        proc.wait()


def test_the_supervisor_brings_a_killed_server_back():
    engine, port = _FakeEngine(safe=True), _free_port()
    sup = _run(["--port", str(port), "--host", "127.0.0.1", "--no-discovery", "--engine-url", engine.url])
    try:
        assert _wait_for(lambda: len(engine.pids()) == 1, 60)
        first = next(iter(engine.pids()))
        subprocess.run(["taskkill", "/F", "/PID", str(first)] if sys.platform == "win32" else ["kill", "-9", str(first)],
                       capture_output=True)
        assert _wait_for(lambda: len(engine.pids()) == 2, 60)
        assert any("restarted (restart 1)" in m for m in engine.messages())
        assert _wait_for(lambda: skymonitor.answers("127.0.0.1", port, 2), 10)
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(sup.pid)], capture_output=True)
        else:
            sup.kill()
        sup.wait()


class _Nina:
    def __init__(self):
        self.calls = []

    async def get(self, path, **kw):
        self.calls.append(path)


def test_the_engine_reconnects_the_sky_monitor_nina_dropped(tmp_path, monkeypatch):
    import asyncio

    from astrodash.observatory import runtime

    sent = []

    async def notify(settings, title, body, **kw):
        sent.append(title)
        return True

    monkeypatch.setattr(runtime.phone, "notify", notify)
    obs = runtime.Observatory(lambda: {"skyMonitor": {"enabled": True}}, _Store(), lambda: tmp_path)
    obs.nina = _Nina()
    obs.sequence = {**obs.sequence, "status": "running"}
    dropped = {"Connected": False, "Name": alpaca.DEVICE_NAME}
    now = time.time()
    run = asyncio.run

    run(obs.guard_safety_monitor(dropped, now))
    assert obs.nina.calls == ["/equipment/safetymonitor/connect"] and "reconnecting" in obs.sky.log[-1]["message"]
    run(obs.guard_safety_monitor(dropped, now + 4))  # not again before SAFETY_RETRY_S
    assert len(obs.nina.calls) == 1
    run(obs.guard_safety_monitor({"Connected": True, "Name": alpaca.DEVICE_NAME}, now + 8))
    assert sent == ["Sky monitor reconnected"] and "reconnected it after 8 s" in obs.sky.log[-1]["message"]

    for i in range(runtime.SAFETY_TRIES + 1):  # NINA will not take it: give up, and say so once
        run(obs.guard_safety_monitor(dropped, now + 100 + i * runtime.SAFETY_RETRY_S))
    run(obs.guard_safety_monitor(dropped, now + 1000))
    assert len(obs.nina.calls) == 1 + runtime.SAFETY_TRIES
    assert sent[-1] == "Sky monitor disconnected" and sent.count("Sky monitor disconnected") == 1

    obs.nina.calls.clear()
    obs._safety_drop = None
    obs.sequence = {**obs.sequence, "status": "idle"}  # you disconnected it outside a sequence: left alone
    run(obs.guard_safety_monitor(dropped, now + 2000))
    obs.sequence = {**obs.sequence, "status": "running"}
    run(obs.guard_safety_monitor({"Connected": False, "Name": "Some other monitor"}, now + 2000))  # not ours
    run(obs.guard_safety_monitor({"Connected": False, "Error": "timeout"}, now + 2000))  # NINA's API failed: not a drop
    assert obs.nina.calls == []
