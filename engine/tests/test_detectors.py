"""Each detector on a synthetic night with one planted problem per episode."""

from datetime import datetime, timedelta, timezone

from astrodash.diagnostics.detectors import analyse, learn_horizon

T0 = datetime(2026, 3, 10, 1, 0, tzinfo=timezone.utc)   # 21:00 local in Montreal
SITE = (45.5, -73.5)


def frame(i, **kw):
    """A clear 300 s Ha frame every 5 minutes; kw overrides any metric."""
    row = {
        "path": f"T/.SessionData/NIGHT_2026-03-09/LIGHT/H/f{i:03d}.fits", "file": f"f{i:03d}.fits",
        "obs_time": (T0 + timedelta(minutes=5 * i)).isoformat(), "rejected": False, "reasons": [],
        "transparency": 1.0, "region_transparency_min": 0.9, "n_stars": 2000, "background_median": 700.0,
        "background_gradient": 1.0, "fwhm_median": 4.0, "eccentricity_median": 0.35, "elongation_consistency": 0.3,
        "sunAlt": -40.0, "n_matched": 500, "ref_transform": [1, 0, 0, 1, 5.0, 3.0], "altitude_deg": 50.0,
    }
    row.update(kw)
    return row


def header(i, **kw):
    h = {"NAXIS1": 3856, "NAXIS2": 2180, "CENTALT": 50.0, "CENTAZ": 200.0, "AMBTEMP": 5.0, "DEWPOINT": 0.0, "FOCTEMP": 4.0, "FOCPOS": 1000,
         "PIERSIDE": "East", "RA": 40.0, "DEC": 60.0}
    h.update(kw)
    return h


def run(rows, headers):
    result = {"lights": [{"key": "Ha", "filter": "Ha", "frames": rows}]}
    return analyse(result, {r["path"]: h for r, h in zip(rows, headers)}, SITE)


def codes(a):
    return {f["code"]: f for f in a["findings"]}


def test_clear_night_has_no_warnings():
    rows = [frame(i) for i in range(20)]
    a = run(rows, [header(i) for i in range(20)])
    assert [f for f in a["findings"] if f["level"] == "warn"] == []


def test_clouds_and_obstruction_are_told_apart():
    rows, hdrs = [], []
    for i in range(30):
        if 10 <= i < 15:      # whole frame dims: clouds
            rows.append(frame(i, transparency=0.3, n_stars=500, rejected=True))
        elif 20 <= i < 24:    # one region dark, rest clear: tree line at low altitude
            rows.append(frame(i, region_transparency_min=0.2, transparency=0.85))
        else:
            rows.append(frame(i))
        hdrs.append(header(i, CENTALT=22.0 + i * 0.1 if 20 <= i < 24 else 50.0, CENTAZ=230.0 if 20 <= i < 24 else 200.0))
    c = codes(run(rows, hdrs))
    assert len(c["clouds"]["frames"]) == 5 and len(c["obstruction"]["frames"]) == 4
    assert "az 230" in c["obstruction"]["text"]
    assert not set(c["clouds"]["frames"]) & set(c["obstruction"]["frames"])


def test_dawn_creep_and_quiet_twilight():
    rows = [frame(i) for i in range(30)]
    rows[27] = frame(27, sunAlt=-15.0, background_median=900.0)   # brightening at dawn
    rows[28] = frame(28, sunAlt=-13.0, background_median=1100.0, transparency=0.4)   # dimmer, but it's dawn
    rows[0] = frame(0, sunAlt=-17.0)                                # dusk, but no brightening
    c = codes(run(rows, [header(i) for i in range(30)]))
    assert c["twilight"]["title"] == "Dawn creep" and len(c["twilight"]["frames"]) == 2
    assert c["twilight-ok"]["level"] == "info"
    assert "clouds" not in c                     # dawn is not reported as clouds


def test_dew_needs_both_low_margin_and_softer_stars():
    rows = [frame(i, fwhm_median=4.0 if i < 15 else 5.2) for i in range(20)]
    hdrs = [header(i, AMBTEMP=3.0, DEWPOINT=2.0 if i >= 15 else -5.0) for i in range(20)]
    c = codes(run(rows, hdrs))
    assert len(c["dew"]["frames"]) == 5


def test_focus_drift_resets_on_focuser_move():
    rows = [frame(i, fwhm_median=4.0 if i < 10 or i >= 15 else 5.0) for i in range(20)]
    # the focuser cools 2 °C during frames 10–14, then an autofocus moves FOCPOS at frame 15
    hdrs = [header(i, FOCTEMP=4.0 - (2.0 if 10 <= i < 15 else 0), FOCPOS=1000 if i < 15 else 1040) for i in range(20)]
    a = run(rows, hdrs)
    assert len(codes(a)["focus"]["frames"]) == 5
    assert any(e["kind"] == "focus" for e in a["events"])


def test_tracking_mispointing_rotation_and_flip():
    rows = [frame(i) for i in range(20)]
    rows[3] = frame(3, eccentricity_median=0.7, elongation_consistency=0.9)       # a run of two …
    rows[4] = frame(4, eccentricity_median=0.68, elongation_consistency=0.85)
    rows[10] = frame(10, eccentricity_median=0.7, elongation_consistency=0.9)     # … and a lone one
    rows[8] = frame(8, ref_transform=[1, 0, 0, 1, 600.0, -400.0])
    rows[12] = frame(12, ref_transform=[0.9986, -0.0523, 0.0523, 0.9986, 4.0, 2.0])   # rotated 3°
    # After the meridian flip the camera is rotated 180° about the frame corner: the translation is about
    # the image size, yet the field centre has only moved by the dither.
    for i in range(16, 20):
        rows[i] = frame(i, ref_transform=[-1, 0, 0, -1, 3856 + 4.0, 2180 - 3.0])
    hdrs = [header(i, PIERSIDE="East" if i < 16 else "West") for i in range(20)]
    a = run(rows, hdrs)
    c = codes(a)
    assert c["tracking"]["frames"] == [rows[3]["path"], rows[4]["path"]] and c["tracking"]["level"] == "warn"
    assert c["tracking-isolated"]["frames"] == [rows[10]["path"]] and c["tracking-isolated"]["level"] == "info"
    assert c["mispointed"]["frames"] == [rows[8]["path"]]
    assert c["rotation"]["frames"] == [rows[12]["path"]]       # the flip is not a rotation change
    assert any(e["kind"] == "flip" for e in a["events"])


def test_learn_horizon_needs_two_obstructed_frames():
    samples = [(232.0, 24.0, True), (238.0, 27.5, True), (235.0, 40.0, False), (100.0, 20.0, True)]
    bins = {b["az"]: b for b in learn_horizon(samples)}
    assert bins[230]["horizon"] == 27.5 and bins[230]["clearMinAlt"] == 40.0
    assert bins[100]["horizon"] is None and bins[100]["blocked"] == 1
