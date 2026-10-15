"""Live observatory: sequence parsing, equipment cards, guiding statistics and diagnosis, the advisor's
validation and measurement, and a PHD2 round trip against the simulator over a real socket."""

import asyncio
import math
import random
import time

from astrodash.observatory import advisor as adv
from astrodash.observatory import equipment, fake, frames, guiding, llm
from astrodash.observatory.nina import parse_sequence
from astrodash.observatory.phd2 import Phd2Client


def test_sequence_path_target_and_progress():
    seq = parse_sequence([
        {"Name": "Start", "Status": "FINISHED", "Items": [{"Name": "Cool", "Status": "FINISHED"}]},
        {"Name": "Targets", "Status": "RUNNING", "Items": [
            {"Name": "M 31", "Status": "RUNNING", "Items": [
                {"Name": "Slew", "Status": "FINISHED"},
                {"Name": "Smart Exposure", "Status": "RUNNING", "ExposureTime": 300, "Filter": {"Name": "Ha"},
                 "Conditions": [{"Name": "Loop", "Iterations": 40, "CompletedIterations": 12}]},
                {"Name": "Smart Exposure", "Status": "CREATED"},
            ]},
        ]},
        {"GlobalTriggers": [{"Name": "Meridian Flip"}]},
    ])
    assert seq["status"] == "running"
    assert seq["path"] == ["Targets", "M 31", "Smart Exposure"]
    assert seq["target"] == "M 31"
    assert seq["current"]["info"] == {"ExposureTime": 300, "Filter": "Ha", "Iterations": 40, "CompletedIterations": 12}
    assert (seq["done"], seq["total"]) == (2, 4)
    assert parse_sequence(None)["status"] == "none"


def test_equipment_cards_only_connected_and_flags_temperature():
    cards = equipment.cards({
        "camera": {"Connected": True, "Name": "ASI", "Temperature": -6.0, "TemperatureSetPoint": -10, "CoolerOn": True, "CoolerPower": 99},
        "rotator": {"Connected": False, "Name": "Rot"},
        "dome": {"Connected": False, "Error": "not configured"},
        "focuser": {"Connected": True, "Position": 18500, "Temperature": 6.4},
    })
    assert [c["key"] for c in cards] == ["camera", "focuser"]
    cam = {f["label"]: f for f in cards[0]["facts"]}
    assert cam["Sensor"]["level"] == "warn" and cam["Cooler"]["level"] == "warn"
    assert cam["Gain" if "Gain" in cam else "Sensor"]  # advanced facts are optional
    assert {f["label"]: f["value"] for f in cards[1]["facts"]}["Position"] == "18500"


def _buffer(ra, dec, ra_ms=None, dec_ms=None, scale=1.0, snr=25.0):
    buf = guiding.GuideBuffer()
    buf.scale = scale
    t0 = time.time() - len(ra) * 2
    for i, (r, d) in enumerate(zip(ra, dec)):
        rm = ra_ms[i] if ra_ms else abs(r) * 100
        dm = dec_ms[i] if dec_ms else abs(d) * 100
        buf.add({"Frame": i, "RADistanceRaw": r, "DECDistanceRaw": d, "SNR": snr, "StarMass": 5000, "HFD": 2.0,
                 "RADuration": rm, "RADirection": "East" if r < 0 else "West", "DECDuration": dm,
                 "DECDirection": "North" if d < 0 else "South"}, t=t0 + 2 * i)
    return buf


def test_diagnosis_tells_ringing_from_seeing_and_deadband():
    rng = random.Random(1)
    n = 150
    ringing = [(1.2 if i % 2 else -1.2) + rng.gauss(0, 0.1) for i in range(n)]
    seeing = [rng.gauss(0, 1.0) for _ in range(n)]
    wander, w = [], 0.0
    for _ in range(n):
        w = 0.95 * w + rng.gauss(0, 0.35)
        wander.append(w)
    buf = _buffer(ringing, wander, dec_ms=[0 if abs(v) < 1.5 else 50 for v in wander])
    st = guiding.stats(buf.window(0, time.time() + 1))
    d = guiding.diagnose(st, target_rms=0.5)
    verdicts = {a["axis"]: a["verdict"] for a in d["axes"]}
    assert verdicts["ra"] == "overcorrecting"
    assert verdicts["dec"] == "deadband_too_wide"
    assert d["actionable"]

    st2 = guiding.stats(_buffer(seeing, seeing[::-1]).window(0, time.time() + 1))
    d2 = guiding.diagnose(st2, target_rms=0.5)
    assert {a["verdict"] for a in d2["axes"]} <= {"seeing_limited", "chasing_seeing"}

    low = guiding.stats(_buffer(seeing, seeing, snr=5).window(0, time.time() + 1))
    assert not guiding.diagnose(low, 0.5)["actionable"]  # low SNR is a caveat: nothing to tune


def test_rms_is_about_zero_and_disturbances_are_excluded():
    buf = _buffer([1.0] * 100, [0.0] * 100)
    dist = guiding.Disturbances()
    st = guiding.stats(buf.window(0, time.time() + 1, dist))
    assert math.isclose(st["ra"]["rms"], 1.0) and st["ra"]["mean"] == 1.0  # a steady offset counts
    mid = buf.samples[50].t
    dist.items.append(guiding.Interval("dither", mid, mid + 20))
    assert len(buf.window(0, time.time() + 1, dist)) == 90


PHD = {"pixelScale": 1.0, "params": {"ra": {"aggression": 0.9, "hysteresis": 0.1, "minMove": 0.15}, "dec": {"aggression": 0.7, "minMove": 0.3, "fastSwitch": 1.0}}}


def test_validate_keeps_safe_suggestions_only():
    assessment, ok, notes = adv.validate({"assessment": "RA rings", "suggestions": [
        {"axis": "ra", "param": "aggression", "value": 0.6, "why": "overshoot"},  # step too big → clamped to 0.8
        {"axis": "dec", "param": "fastSwitch", "value": 0},  # not tunable
        {"axis": "dec", "param": "predictWeight", "value": 0.5},  # algorithm has no such param
        {"axis": "ra", "param": "hysteresis", "value": 3.0},  # far outside range
    ]}, PHD)
    assert assessment == "RA rings"
    assert [(s["axis"], s["param"], s["value"]) for s in ok] == [("ra", "aggression", 0.8)]
    assert ok[0]["note"] and len(notes) == 3
    # minMove range follows the pixel scale (0.10–0.70″ at 2″/px → 0.05–0.35 px)
    assert adv.resolved_bounds("minMove", 2.0)["hi"] == 0.35


class _Store:
    def __init__(self):
        self.data = {}

    def get_json(self, k):
        return self.data.get(k)

    def set_json(self, k, v):
        self.data[k] = v


def test_change_is_measured_before_vs_after():
    a = adv.Advisor(_Store())
    rng = random.Random(3)
    now = time.time()
    t_change = now - adv.SETTLE_S - adv.WINDOW_S - 5
    buf = guiding.GuideBuffer()
    buf.scale = 1.0
    for i in range(300):  # 1.0″ before the change, 0.5″ after
        t = t_change - adv.WINDOW_S + i * 2
        sd = 1.0 if t < t_change else 0.5
        buf.add({"RADistanceRaw": rng.gauss(0, sd), "DECDistanceRaw": rng.gauss(0, sd), "HFD": 2.0, "SNR": 20}, t=t)
    before = guiding.stats(buf.window(t_change - adv.WINDOW_S, t_change))
    entry = a.record_change("ra", "aggression", 0.9, 0.8, "ai", before)
    entry["t"], entry["measureAt"] = t_change, now - 1
    a.measure(buf, guiding.Disturbances())
    assert entry["verdict"] == "better", entry["outcome"]
    # a newer change voids a pending one
    e2 = a.record_change("ra", "aggression", 0.8, 0.75, "manual", before)
    a.record_change("dec", "minMove", 0.3, 0.25, "manual", before)
    assert e2["verdict"] == "void"


def test_extract_json_survives_think_blocks_and_fences():
    assert llm.extract_json('<think>{"x": 1}</think>Sure:\n```json\n{"assessment": "ok", "suggestions": []}\n```') == {"assessment": "ok", "suggestions": []}
    assert llm.extract_json('prefix {"a": "b \\" }", "c": {"d": 1}} suffix') == {"a": 'b " }', "c": {"d": 1}}
    assert llm.extract_json("no json") is None


def test_frame_flags_compare_within_a_filter():
    hist = [{"ImageType": "LIGHT", "Filter": "L", "HFR": 2.0, "Stars": 900, "Date": None} for _ in range(5)]
    hist += [{"ImageType": "LIGHT", "Filter": "Ha", "HFR": 2.0, "Stars": 200, "Date": None} for _ in range(4)]  # fewer stars is normal in Ha
    hist += [{"ImageType": "LIGHT", "Filter": "L", "HFR": 3.0, "Stars": 300, "Date": None}]
    out = frames.tonight(hist)
    assert len(out[0]["flags"]) == 2 and not any(f["flags"] for f in out[1:])


def test_phd2_round_trip_against_simulator():
    async def run():
        sim = fake.Sim()
        sim.exposure_ms = 50
        server = fake.FakePhd2(sim)
        port = await server.start()
        steps = []
        client = Phd2Client("127.0.0.1", port, on_event=lambda m: m.get("Event") == "GuideStep" and steps.append(m))
        task = asyncio.create_task(client.run())
        for _ in range(100):
            await asyncio.sleep(0.05)
            if client.state.params and len(steps) >= 3:
                break
        st = client.state
        assert st.connected and st.app_state == "Guiding" and st.pixel_scale == 1.6
        assert st.algorithms == {"ra": "Hysteresis", "dec": "ResistSwitch"}
        assert st.params["ra"]["aggression"] == 0.95 and st.calibrated
        assert await client.set_param("ra", "aggression", 0.8) == 0.8 and sim.params["ra"]["aggression"] == 0.8
        png = frames.star_png(await client.star_image(31))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(steps) >= 3
        await client.close()
        await server.stop()
        task.cancel()

    asyncio.run(run())


def test_clock_offset_from_date_headers_converges_below_a_second():
    from email.utils import formatdate

    from astrodash.observatory.nina import ClockOffset

    true_offset = 16.37  # the observatory PC runs 16.37 s ahead
    clock = ClockOffset()
    t = 1_800_000_000.0
    rng = random.Random(5)
    for _ in range(30):
        t += rng.uniform(3.0, 5.0)
        t0, t1 = t, t + rng.uniform(0.01, 0.08)
        served = rng.uniform(t0, t1) + true_offset
        clock.add(formatdate(served, usegmt=True), t0, t1)  # whole seconds, like a real Date header
    assert abs(clock.seconds - true_offset) < 0.3
    assert ClockOffset().seconds == 0.0


def test_sequence_names_drop_ninas_container_suffix():
    seq = parse_sequence([{"Name": "Targets_Container", "Status": "RUNNING", "Items": [
        {"Name": "IC 405_Container", "Status": "RUNNING", "Items": [
            {"Name": "Robocopy Start", "Status": "FINISHED"},
            {"Name": "ImagingNew_Container", "Status": "RUNNING", "Items": [{"Name": "Smart Exposure", "Status": "RUNNING"}]},
        ]},
        {"Name": "M 81, M 82 v1.2", "Status": "CREATED", "Items": []},
    ]}])
    assert seq["path"] == ["Targets", "IC 405", "ImagingNew", "Smart Exposure"]
    assert seq["target"] == "IC 405"
    assert [s["name"] for s in seq["steps"]][-1] == "M 81, M 82 v1.2"  # human names are not cut at commas or dots


def test_tonights_frames_endpoint_counts_the_whole_night(tmp_path):
    from fastapi.testclient import TestClient

    from astrodash.api import create_app
    from astrodash.config import Config

    app = create_app(Config(data_root=tmp_path / "root", db_path=tmp_path / "var" / "t.db"), scan_on_start=False)
    with TestClient(app) as client:
        obs = app.state.engine.observatory
        names = ["Ha", "OIII", "SII"] * 50 + ["Ha"] * 5  # 155 lights, more than the snapshot's 60
        obs.frames = [{"index": i, "type": "LIGHT", "filter": f, "date": None} for i, f in enumerate(reversed(names))]
        obs.frames.append({"index": 999, "type": "FLAT", "filter": "Ha", "date": None})
        d = client.get("/api/live/frames").json()
        assert d["total"] == 155 and d["counts"] == {"H": 55, "O": 50, "S": 50} and len(d["frames"]) == 100
        d = client.get("/api/live/frames", params={"filter": "O", "limit": 10}).json()
        assert d["selected"] == 50 and len(d["frames"]) == 10 and {f["filterKey"] for f in d["frames"]} == {"O"}
        assert client.get("/api/live/frames", params={"all": "true"}).json()["total"] == 156
