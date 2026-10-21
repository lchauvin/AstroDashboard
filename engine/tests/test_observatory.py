"""Live observatory: sequence parsing, equipment cards, guiding statistics and diagnosis, the advisor's
validation and measurement, and a PHD2 round trip against the simulator over a real socket."""

import asyncio
import math
import random
import time

import pytest

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


def test_ollama_url_at_ollama_com_uses_the_cloud_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert llm.key_status("ollama/llama3.1:8b")["ok"]
    assert not llm.key_status("ollama/gpt-oss:120b", "https://ollama.com")["ok"]
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    assert llm._endpoint("ollama", "https://ollama.com/") == ("https://ollama.com/v1", "OLLAMA_API_KEY")
    assert llm._endpoint("ollama", "http://192.168.2.200:11434") == (None, None)


def test_reasoning_effort_steps_down_when_rejected_and_only_none_is_capped(monkeypatch):
    import httpx
    sent = []

    def post(url, json, timeout, **_):
        sent.append((json.get("reasoning_effort"), json.get("max_tokens"), timeout))
        if json.get("reasoning_effort") is not None:
            return httpx.Response(400, text=f"unsupported reasoning_effort {json['reasoning_effort']!r}")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}})

    monkeypatch.setattr(llm.httpx, "post", post)
    monkeypatch.setattr(llm, "_UNSUPPORTED", set())
    assert llm.call("ollama/m", "s", "p")["text"] == "{}"
    assert sent == [("none", 3000, 120.0), ("low", None, 600.0), (None, None, 600.0)]
    sent.clear()
    llm.call("ollama/m", "s", "p")  # remembered: no more rejected round trips
    assert sent == [(None, None, 600.0)]
    sent.clear()
    llm.call("ollama/n", "s", "p", effort="high")
    assert sent == [("high", None, 600.0), (None, None, 600.0)]
    with pytest.raises(llm.LlmError):
        llm.call("ollama/n", "s", "p", effort="max")


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


# ---------------------------------------------------------------------- polar alignment

from astrodash.observatory import polar  # noqa: E402


def _guided(drift_north, seconds=300.0, sign=1, rate=4.0, scale=1.5, t0=None, seed=1, buf=None):
    """PHD2 guiding a mount whose pointing drifts `drift_north` ″/min; `sign` is the residual→pulse convention."""
    rng = random.Random(seed)
    buf = buf or guiding.GuideBuffer()
    buf.scale = scale
    t0 = time.time() - seconds if t0 is None else t0
    err = 0.0  # px; with sign +1 a positive residual is the pointing south of the lock
    for i in range(int(seconds / 2)):
        err += -sign * drift_north / 60 * 2 / scale
        measured = err + rng.gauss(0, 0.15)
        corr = 0.7 * measured if abs(measured) > 0.2 else 0.0
        err -= corr
        buf.add({"Frame": i, "RADistanceRaw": 0.0, "DECDistanceRaw": measured, "SNR": 20, "HFD": 2.0,
                 "DECDuration": abs(corr) / rate * 1000, "DECDirection": "North" if sign * corr > 0 else "South"}, t=t0 + 2 * i)
    return buf


def test_dec_drift_is_read_back_from_the_corrections_whatever_the_sign_convention():
    for sign in (1, -1):
        buf = _guided(1.2, sign=sign)
        est = polar.window_rate(list(buf.samples), None, 4.0, 1.5, polar.pulse_sign(list(buf.samples)))
        assert abs(est["rate"] - 1.2) < 0.2, (sign, est)  # σ ≈ 0.07″/min per window
    # a dither in the middle: the samples inside it are dropped and the segments still add up
    buf = _guided(-0.8)
    mid = buf.samples[75].t
    dist = guiding.Disturbances(items=[guiding.Interval("dither", mid, mid + 40)])
    est = polar.window_rate(list(buf.samples), dist, 4.0, 1.5, 1)
    assert abs(est["rate"] + 0.8) < 0.2 and est["clean"] < 270
    assert polar.window_rate(list(buf.samples)[:40], None, 4.0, 1.5, 1) is None  # < 2 min clean


def _windows(alt, az, lat=45.5, hours=(-3.0, 1.0), noise=0.1, seed=2):
    rng = random.Random(seed)
    out, h, t = [], hours[0], 0.0
    while h <= hours[1]:
        r = polar.K * (az * math.cos(math.radians(lat)) * math.cos(h * math.pi / 12) - alt * math.sin(h * math.pi / 12))
        out.append({"t": t, "rate": r + rng.gauss(0, noise), "clean": 280, "n": 140, "ha": h, "dec": 41.0, "alt": 50.0,
                    "pier": "East" if h < 0 else "West", "lat": lat, "epoch": 0})
        h, t = h + 5 / 60, t + 300
    return out


def test_fit_splits_altitude_from_azimuth_and_says_which_way():
    f = polar.fit(_windows(6.0, -10.0))
    assert f["ok"] and not f["pierFlipped"], f
    assert abs(f["alt"] - 6.0) < 1.0 and abs(f["az"] + 10.0) < 1.5, f
    assert polar.advice(f) == ["lower the altitude by about 6′", "turn the azimuth east by about 10′"]
    assert not polar.fit(_windows(6.0, -10.0, hours=(-1.0, -0.2)))["ok"]  # not enough hour angle
    # a Dec sense that flips with the pier: the sizes survive, the directions are withheld
    flipped = [{**w, "rate": -w["rate"]} if w["pier"] == "West" else w for w in _windows(6.0, -10.0)]
    g = polar.fit(flipped)
    assert g["pierFlipped"] and abs(abs(g["alt"]) - 6.0) < 1.0 and polar.advice(g) == []


def test_a_mount_that_moves_starts_a_new_epoch():
    tracker = polar.PolarTracker(_Store())
    ws = _windows(3.0, 4.0, hours=(-1.0, -0.5))
    tracker.windows = ws[:-2]
    assert tracker._check_shift() is None
    tracker.windows = ws[:-2] + [{**w, "rate": w["rate"] + 1.5} for w in ws[-2:]]
    shift = tracker._check_shift()
    assert shift and tracker.epoch == 1 and abs(shift["delta"] - 1.5) < 0.3
    assert [w["epoch"] for w in tracker.windows[-3:]] == [0, 1, 1]
    s = tracker.summary(now=tracker.windows[-1]["t"] + 10)
    assert s["now"]["windows"] == 2 and s["shifts"] == [shift]


def test_tracker_closes_wall_clock_windows_with_the_mount_context():
    store = _Store()
    tracker = polar.PolarTracker(store)
    end = math.floor(time.time() / polar.WINDOW_S) * polar.WINDOW_S
    buf = _guided(0.9, seconds=polar.WINDOW_S, t0=end - polar.WINDOW_S)
    mount = {"Connected": True, "SiderealTime": 1.5, "RightAscension": 23.0, "Declination": 41.0, "Altitude": 25.0,
             "SideOfPier": "pierEast", "SiteLatitude": 45.5}
    tracker.update(buf, guiding.Disturbances(), {"yRate": 4.0}, mount, now=end + 5)
    (w,) = tracker.windows
    assert abs(w["rate"] - 0.9) < 0.2 and w["pier"] == "East" and abs(w["ha"] - 2.458) < 0.01
    assert store.data["polar_log"]["windows"] == [w]
    tracker.update(buf, guiding.Disturbances(), {"yRate": 4.0}, mount, now=end + 30)  # same window: no work
    s = tracker.summary(now=end + 30)
    assert len(tracker.windows) == 1 and abs(s["now"]["minArcmin"] - 0.9 / polar.K) < 0.5
    assert any("refraction" in c for c in s["caveats"])
    assert adv.polar_line(s).startswith("POLAR ALIGNMENT: Dec drift +")
    t2 = polar.PolarTracker(_Store())
    t2.update(buf, guiding.Disturbances(), None, mount, now=end + 5)
    assert t2.status and not t2.windows  # no calibration, no window


def test_analyze_now_returns_at_once_and_the_model_answers_in_the_background(monkeypatch, tmp_path):
    import threading

    from astrodash.observatory import runtime

    settings = {"advisor": {"enabled": True, "model": "ollama/m", "effort": "low"}}
    obs = runtime.Observatory(lambda: settings, _Store(), tmp_path)
    release = threading.Event()

    def slow_call(*_a, **_k):  # a reasoning model: longer than the web proxy would wait
        assert release.wait(5)
        return {"text": '{"assessment": "fine", "suggestions": []}', "latencyMs": 1, "model": "ollama/m", "note": ""}

    monkeypatch.setattr(obs, "recent_stats", lambda: {"n": 100})
    monkeypatch.setattr(runtime.guiding, "diagnose", lambda st, target: {"actionable": True})
    monkeypatch.setattr(obs, "_context", lambda st, diag: {})
    monkeypatch.setattr(runtime.adv, "build_prompt", lambda ctx: "p")
    monkeypatch.setattr(runtime.adv, "_brief", lambda st: None)
    monkeypatch.setattr(obs, "_phd2_dict", lambda: {"params": {}})
    monkeypatch.setattr(runtime.llm, "call", slow_call)

    async def run():
        obs.start_analysis()
        assert obs.advisor.busy
        with pytest.raises(RuntimeError, match="already running"):
            obs.start_analysis()
        release.set()
        await obs._analysis_task
        assert not obs.advisor.busy
        assert obs.advisor.analyses[-1]["assessment"] == "fine"
        assert obs.advisor.analyses[-1]["effort"] == "low"

    asyncio.run(run())


class _FakePhd2:
    def __init__(self, params):
        self.connected = True
        self.state = type("S", (), {"app_state": "Guiding", "params": params})()

    async def set_param(self, axis, name, value):
        self.state.params[axis][name] = value
        return value


def test_auto_apply_takes_the_top_suggestion_skips_when_unsafe_and_reverts_a_worse_one(monkeypatch, tmp_path):
    from astrodash.observatory import runtime

    settings = {"advisor": {"enabled": True, "model": "ollama/m", "autoApply": True}}
    obs = runtime.Observatory(lambda: settings, _Store(), tmp_path)
    obs.phd2 = _FakePhd2({"ra": {"aggression": 0.7, "hysteresis": 0.1}, "dec": {}})
    stats = {"total": 1.0, "ra": {"rms": 0.7}, "dec": {"rms": 0.6}, "n": 150, "se": 0.03, "hfdPx": 2.0}
    monkeypatch.setattr(obs, "recent_stats", lambda: stats)
    reply = '{"assessment": "RA overshoots", "suggestions": [' \
            '{"axis": "ra", "param": "aggression", "value": 0.6}, {"axis": "ra", "param": "hysteresis", "value": 0.15}]}'
    monkeypatch.setattr(runtime.llm, "call", lambda *a, **k: {"text": reply, "latencyMs": 1, "model": "m", "note": ""})
    monkeypatch.setattr(obs, "_context", lambda st, diag: {})
    monkeypatch.setattr(runtime.adv, "build_prompt", lambda ctx: "p")
    monkeypatch.setattr(runtime.guiding, "diagnose", lambda st, target: {"actionable": True})
    monkeypatch.setattr(obs, "_phd2_dict", lambda: {"params": obs.phd2.state.params})

    async def run():
        entry = await obs.analyze(manual=False)
        assert obs.phd2.state.params["ra"] == {"aggression": 0.6, "hysteresis": 0.1}  # the top one only
        assert entry["auto"].startswith("Applied RA aggression 0.7 → 0.6")
        change = obs.advisor.changes[-1]
        assert (change["source"], change["verdict"]) == ("auto", "pending")

        entry = await obs.analyze(manual=False)  # still measuring: nothing applied
        assert "still being measured" in entry["auto"]
        assert obs.phd2.state.params["ra"]["aggression"] == 0.6

        change["verdict"] = "worse"
        await obs._auto_revert()
        assert obs.phd2.state.params["ra"]["aggression"] == 0.7
        assert obs.advisor.changes[-1]["source"] == "auto-revert" and change["reverted"]
        await obs._auto_revert()  # once only
        assert obs.advisor.changes[-1]["source"] == "auto-revert" and len(obs.advisor.changes) == 2

        obs.advisor.changes[-1]["verdict"] = "neutral"
        entry = await obs.analyze(manual=False)  # 0.7 → 0.6 measured worse tonight: not again
        assert "made guiding worse earlier tonight" in entry["auto"]
        assert obs.phd2.state.params["ra"]["aggression"] == 0.7

    asyncio.run(run())


def test_tonight_leaves_out_unsaved_snapshots():
    rows = frames.tonight([{"ImageType": "LIGHT", "Filename": "a.fits"}, {"ImageType": "SNAPSHOT", "Filename": None},
                           {"ImageType": "SNAPSHOT", "Filename": "s.fits"}])
    assert [r["index"] for r in rows] == [2, 0]


PHD_ALGOS = {**PHD, "algorithms": {"ra": "Hysteresis", "dec": "ResistSwitch"}}


def test_validate_takes_one_algorithm_switch_and_drops_that_axis_parameters():
    _, ok, notes = adv.validate({"assessment": "RA periodic", "suggestions": [
        {"axis": "ra", "param": "aggression", "value": 0.8},  # the old algorithm's: goes with the switch
        {"axis": "ra", "param": "algorithm", "value": "ppec", "why": "periodic error"},
        {"axis": "dec", "param": "algorithm", "value": "Lowpass2"},  # a second switch
        {"axis": "dec", "param": "minMove", "value": 0.25},
    ]}, PHD_ALGOS)
    assert [(s["axis"], s["param"], s["current"], s["value"]) for s in ok] == [
        ("ra", "algorithm", "Hysteresis", "Predictive PEC"), ("dec", "minMove", 0.3, 0.25)]
    assert any("one algorithm switch" in n for n in notes) and any("replaces" in n for n in notes)

    _, ok, notes = adv.validate({"suggestions": [
        {"axis": "dec", "param": "algorithm", "value": "Predictive PEC"},  # RA only in PHD2
        {"axis": "dec", "param": "dec algorithm", "value": "Resist Switch"},  # already in use
        {"axis": "ra", "param": "algorithmName", "value": "Lowpass"},  # left to you
    ]}, PHD_ALGOS)
    assert ok == [] and len(notes) == 3


def test_prompt_lists_the_algorithms_and_tonight_switches():
    st = {"hfdPx": 2.0, "snr": 20.0, "mass": 5000, "total": 1.0, "se": 0.03, "n": 150, "seconds": 300, "oscillation": 1.7,
          **{a: {"rms": 0.7, "peak": 2.0, "mean": 0.0, "lag1": 0.6, "idle": 0.1, "flips": 0.3, "pulseMs": 100, "limited": 0.0}
             for a in ("ra", "dec")}}
    ctx = {"stats": st, "diagnosis": {"axes": [], "caveats": []}, "phd2": PHD_ALGOS, "targetRms": 0.8,
           "changes": [{"axis": "ra", "param": "algorithm", "old": "Hysteresis", "new": "Predictive PEC", "source": "ai",
                        "outcome": "measuring…"}]}
    prompt = adv.build_prompt(ctx)
    assert "  ra [Hysteresis] | Lowpass2 | ResistSwitch | Predictive PEC" in prompt
    assert "  dec [ResistSwitch] | Lowpass2 | Hysteresis" in prompt
    assert "ra.algorithm Hysteresis -> Predictive PEC (ai)" in prompt


def test_algorithm_switch_waits_for_done_when_suggested_and_is_logged_at_once_when_not(monkeypatch, tmp_path):
    from astrodash.observatory import runtime

    obs = runtime.Observatory(lambda: {}, _Store(), tmp_path)
    phd = _FakePhd2({"ra": {"minMove": 0.2}, "dec": {}})
    phd.state.algorithms = {"ra": "Hysteresis", "dec": "ResistSwitch"}

    async def refresh_params():
        pass

    phd.refresh_params = refresh_params
    obs.phd2 = phd
    old_algo = {"total": 1.2, "ra": {"rms": 0.9}, "dec": {"rms": 0.6}, "n": 150, "se": 0.03, "hfdPx": 2.0}
    monkeypatch.setattr(obs, "recent_stats", lambda: old_algo)
    sg = {"id": "s1", "axis": "ra", "param": "algorithm", "current": "Hysteresis", "value": "Predictive PEC", "status": "open"}
    other = {"id": "s2", "axis": "ra", "param": "aggression", "current": 0.9, "value": 0.8, "status": "open"}
    obs.advisor.analyses.append({"t": time.time(), "stats": None, "suggestions": [sg, other]})

    with pytest.raises(ValueError, match="still reports Hysteresis"):  # Done before the switch
        asyncio.run(obs.algorithm_done("s1"))
    obs._on_algorithms({"ra": "Hysteresis", "dec": "ResistSwitch"}, {"ra": "Predictive PEC", "dec": "ResistSwitch"})
    assert obs.advisor.changes == [] and sg["status"] == "open"  # seen, waiting for Done
    phd.state.algorithms["ra"] = "Predictive PEC"
    monkeypatch.setattr(obs, "recent_stats", lambda: None)  # by Done, the guiding is the new algorithm's

    asyncio.run(obs.algorithm_done("s1"))
    c = obs.advisor.changes[-1]
    assert (c["axis"], c["param"], c["old"], c["new"], c["source"], c["suggestion"]) == (
        "ra", "algorithm", "Hysteresis", "Predictive PEC", "ai", "s1")
    assert c["before"]["total"] == 1.2 and c["verdict"] == "pending"  # the guiding before the switch
    assert c["settleS"] == adv.ALGORITHM_SETTLE_S["Predictive PEC"] and c["measureAt"] - c["t"] > 600
    assert (sg["status"], other["status"]) == ("applied", "stale")  # the old algorithm's suggestion is moot
    with pytest.raises(ValueError, match="applied"):
        asyncio.run(obs.algorithm_done("s1"))

    obs._on_algorithms({"dec": "ResistSwitch"}, {"dec": "Lowpass2"})  # a switch of your own: no Done needed
    assert obs.advisor.changes[-1]["source"] == "manual" and len(obs.advisor.changes) == 2


def test_done_without_a_seen_switch_measures_against_the_analysed_guiding(tmp_path):
    from astrodash.observatory import runtime

    obs = runtime.Observatory(lambda: {}, _Store(), tmp_path)
    phd = _FakePhd2({"ra": {}, "dec": {"minMove": 0.2}})
    phd.state.algorithms = {"ra": "Hysteresis", "dec": "Lowpass2"}  # switched while the engine was down

    async def refresh_params():
        pass

    phd.refresh_params = refresh_params
    obs.phd2 = phd
    brief = {"total": 1.1, "ra": 0.7, "dec": 0.8, "n": 140, "se": 0.04, "hfdPx": 2.1}
    obs.advisor.analyses.append({"t": time.time(), "stats": brief, "suggestions": [
        {"id": "d1", "axis": "dec", "param": "algorithm", "current": "ResistSwitch", "value": "Lowpass2", "status": "open"}]})
    asyncio.run(obs.algorithm_done("d1"))
    assert obs.advisor.changes[-1]["before"] == brief


def test_phd2_client_reports_an_algorithm_switch_once():
    from astrodash.observatory.phd2 import Phd2Client

    seen = []
    client = Phd2Client("x", 1, on_algorithms=lambda old, new: seen.append((dict(old), dict(new))))
    phd = {"ra": "Hysteresis", "dec": "ResistSwitch"}

    async def call(method, params=None, **_k):
        if method == "get_algo_param_names":
            return ["algorithmName", "minMove"]
        return phd[params[0]] if params[1] == "algorithmName" else 0.2

    client.call = call

    async def run():
        await client.refresh_params()  # the first read on connect: nothing to report
        phd["ra"] = "Predictive PEC"
        await asyncio.gather(client.refresh_params(), client.refresh_params())  # a ConfigurationChange burst
    asyncio.run(run())
    assert seen == [({"ra": "Hysteresis", "dec": "ResistSwitch"}, {"ra": "Predictive PEC", "dec": "ResistSwitch"})]


def test_auto_apply_leaves_an_algorithm_switch_to_you(monkeypatch, tmp_path):
    from astrodash.observatory import runtime

    settings = {"advisor": {"enabled": True, "model": "ollama/m", "autoApply": True}}
    obs = runtime.Observatory(lambda: settings, _Store(), tmp_path)
    obs.phd2 = _FakePhd2({"ra": {"aggression": 0.7}, "dec": {}})
    monkeypatch.setattr(obs, "recent_stats", lambda: {"total": 1.0, "ra": {"rms": 0.7}, "dec": {"rms": 0.6}, "n": 150, "se": 0.03, "hfdPx": 2.0})
    reply = '{"assessment": "RA periodic", "suggestions": [{"axis": "ra", "param": "algorithm", "value": "Predictive PEC"}]}'
    monkeypatch.setattr(runtime.llm, "call", lambda *a, **k: {"text": reply, "latencyMs": 1, "model": "m", "note": ""})
    monkeypatch.setattr(obs, "_context", lambda st, diag: {})
    monkeypatch.setattr(runtime.adv, "build_prompt", lambda ctx: "p")
    monkeypatch.setattr(runtime.guiding, "diagnose", lambda st, target: {"actionable": True})
    monkeypatch.setattr(obs, "_phd2_dict", lambda: {"params": obs.phd2.state.params, "algorithms": {"ra": "Hysteresis", "dec": "ResistSwitch"}})

    entry = asyncio.run(obs.analyze(manual=False))
    assert entry["suggestions"][0]["value"] == "Predictive PEC"
    assert "Brain" in entry["auto"] and obs.advisor.changes == []
