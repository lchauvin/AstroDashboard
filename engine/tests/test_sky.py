"""The sky monitor (clouds from PHD2's guide star) and the ASCOM Alpaca safety monitor NINA reads it through."""

import socket
import time

import httpx
from fastapi.testclient import TestClient

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
    obs = runtime.Observatory(lambda: {"skyMonitor": {"enabled": True}}, _SiteStore(sites), tmp_path)
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

    obs = runtime.Observatory(lambda: {}, _Store(), tmp_path)  # a store without sites
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
    c.put(f"{dev}/disconnect")
    assert c.get(f"{dev}/connected").json()["Value"] is False
    c.put(f"{dev}/connect")
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


def test_alpaca_server_runs_in_its_own_thread_and_says_when_its_port_is_taken():
    m = SkyMonitor(_Store())
    port = _free_port()
    server = alpaca.AlpacaServer(m, "127.0.0.1", port, discovery=False)
    server.start()
    try:
        answer = None
        for _ in range(50):
            try:
                answer = httpx.get(f"http://127.0.0.1:{port}/api/v1/safetymonitor/0/name", timeout=2).json()
                break
            except httpx.TransportError:
                time.sleep(0.1)
        assert answer and answer["Value"] == alpaca.DEVICE_NAME and m.server["running"]
    finally:
        server.stop()
    assert not m.server["running"] and m.server["error"] is None

    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        taken = alpaca.AlpacaServer(m, "127.0.0.1", blocker.getsockname()[1], discovery=False)
        taken.start()
        taken._thread.join(5)
    assert "Could not listen on port" in m.server["error"] and not m.server["running"]
