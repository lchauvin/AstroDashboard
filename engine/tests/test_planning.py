import numpy as np
import pytest
from fastapi.testclient import TestClient

from astrodash import halpha, planning
from astrodash.api import create_app
from astrodash.config import Config


@pytest.mark.parametrize("raw, expected", [
    (45.544, 45.544),
    ("45.544", 45.544),
    ("45 32 39", 45.5441667),
    ("+45:32:39", 45.5441667),
    ("-73 32 15", -73.5375),
    ("73 32 15 W", -73.5375),
    ("45°32'39\"", 45.5441667),
    ("nonsense", None),
    (None, None),
])
def test_parse_angle(raw, expected):
    got = planning.parse_angle(raw)
    assert (got is None) if expected is None else got == pytest.approx(expected, abs=1e-6)


def test_site_from_header_treats_zero_elevation_as_unset():
    site = planning.site_from_header({"SITELAT": 45.5441666, "SITELONG": -73.5375, "SITEELEV": 0.0, "OBJECT": "M 31"})
    assert site["lat"] == pytest.approx(45.5441666)
    assert site["lon"] == pytest.approx(-73.5375)
    assert site["elevation"] is None and site["notes"]
    alt = planning.site_from_header({"LAT-OBS": "45 32 39", "LONG-OBS": "286.4625", "ALT-OBS": 512})
    assert alt["lon"] == pytest.approx(-73.5375) and alt["elevation"] == 512 and alt["keys"]["lat"] == "LAT-OBS"


def test_cluster_locations_groups_within_5_km():
    locs = [
        {"lat": 45.544, "lon": -73.537, "date": "2026-01-01", "target": "A"},
        {"lat": 45.546, "lon": -73.540, "date": "2026-01-02", "target": "B"},
        {"lat": 45.455, "lon": -71.152, "date": "2026-02-01", "target": "A"},  # Mont-Mégantic, ~190 km away
    ]
    clusters = planning.cluster_locations(locs)
    assert [c["nights"] for c in clusters] == [2, 1]
    assert clusters[0]["targets"] == ["A", "B"]


def test_normalize_site_validates():
    with pytest.raises(ValueError):
        planning.normalize_site({"name": "", "lat": 1, "lon": 1})
    with pytest.raises(ValueError):
        planning.normalize_site({"name": "x", "lat": 95, "lon": 1})
    s = planning.normalize_site({"name": " Dark ", "lat": "45 27 18", "lon": -71.15, "sqm": 21.2,
                                 "horizon": [{"az": 200, "alt": 25}, {"az": 10, "alt": 15}, {"az": 400, "alt": 3}]})
    assert s["name"] == "Dark" and s["minAlt"] == 20
    assert [p["az"] for p in s["horizon"]] == [10, 200]


def test_sites_goals_settings_api(data_root, tmp_path):
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "p.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        app.state.engine._run_scan()
        sites = client.get("/api/sites").json()["sites"]
        # Home is pre-filled from the headers of the fixture lights (45.5, -73.5)
        assert len(sites) == 1 and sites[0]["name"] == "Home" and sites[0]["isDefault"]
        assert sites[0]["nights"] >= 1
        home = sites[0]["id"]
        assert client.delete(f"/api/sites/{home}").status_code == 409  # at least one site

        dark = client.post("/api/sites", json={"name": "Dark site", "lat": 45.455, "lon": -71.152, "sqm": 21.3,
                                               "isDefault": True}).json()["site"]
        listed = {s["id"]: s for s in client.get("/api/sites").json()["sites"]}
        assert listed[dark["id"]]["isDefault"] and not listed[home]["isDefault"]
        assert client.post("/api/sites", json={"name": "Bad", "lat": 100, "lon": 0}).status_code == 422
        assert client.delete(f"/api/sites/{dark['id']}").json()["ok"]

        sugg = client.get("/api/sites/suggest").json()["suggestions"]
        assert sugg[0]["knownSiteName"] == "Home"

        rev = "ic-1848-soul-nebula"
        sess = client.get(f"/api/revisions/{rev}").json()["sessions"][0]
        assert sess["siteName"] == "Home"
        from_sess = client.get("/api/sites/from-session", params={"revisionId": rev, "session": sess["folder"]}).json()
        assert from_sess["site"]["lat"] == pytest.approx(45.5)

        assert client.put(f"/api/goals/{rev}", json={"goals": {"H": {"mode": "snr"}}}).status_code == 422
        client.put(f"/api/goals/{rev}", json={"goals": {"H": {"mode": "snr", "snr": 30}, "O": {"mode": "hours", "hours": 15}}})
        assert client.get(f"/api/revisions/{rev}").json()["goals"]["O"] == {"mode": "hours", "hours": 15}
        ctx = client.get("/api/planning/context").json()
        t = next(t for t in ctx["targets"] if t["id"] == rev)
        assert t["goalsSource"] == "dashboard" and t["pointing"]["dec"] == pytest.approx(40.0)
        assert {f["filter"] for f in t["filters"]} >= {"H"}
        assert ctx["detectedRig"]["focalLength"] == 250.0

        assert client.put("/api/settings", json={"goalCapHours": 0}).status_code == 422
        assert client.put("/api/settings", json={"bogus": 1}).status_code == 422
        assert client.put("/api/settings", json={"goalCapHours": 30}).json()["settings"]["goalCapHours"] == 30

        # Notifications: partial updates keep the defaults; the topic is checked; the state round-trips.
        notify = client.put("/api/settings", json={"notify": {"ntfyTopic": " astro-x7k2 "}}).json()["settings"]["notify"]
        assert notify["ntfyTopic"] == "astro-x7k2" and notify["ntfyServer"] == "https://ntfy.sh" and not notify["enabled"]
        assert client.put("/api/settings", json={"notify": {"ntfyTopic": "a/b"}}).status_code == 422
        assert client.put("/api/settings", json={"notify": {"dashboardUrl": "ftp://x"}}).status_code == 422
        assert client.put("/api/settings", json={"notify": {"bogus": 1}}).status_code == 422
        assert client.get("/api/notify/state").json()["state"] is None
        client.put("/api/notify/state", json={"date": "2026-10-18", "sent": True, "level": "excellent"})
        assert client.get("/api/notify/state").json()["state"]["sent"] is True


def test_pointing_wraps_ra_through_zero():
    from astrodash.library import _pointing

    class F:
        def __init__(self, ra):
            self.header = {"RA": ra, "DEC": 60.0}

    p = _pointing([F(359.0), F(1.0)])
    assert p["ra"] in (0.0, 360.0) or p["ra"] == pytest.approx(0.0, abs=1e-6)


def test_halpha_measure_resolved_vs_diluted():
    # A synthetic survey: 20 R background, a 120' disc of +200 R at the centre.
    def fake(ra, dec, fov, size):
        y, x = np.mgrid[0:size, 0:size]
        px_per_arcmin = size / (fov * 60)
        r_arcmin = np.hypot(x - (size - 1) / 2, y - (size - 1) / 2) / px_per_arcmin
        return np.where(r_arcmin <= 60, 220.0, 20.0)

    big = halpha.measure(314.8, 44.5, 120, 120, 0, fetch=fake)
    assert big["netR"] == pytest.approx(200, rel=0.02) and not big["beamDiluted"]
    small = halpha.measure(314.8, 44.5, 20, 10, 0, fetch=fake)
    assert small["beamDiluted"]


def test_halpha_lookup_returns_cached_and_queues_missing(data_root, tmp_path, monkeypatch):
    calls = []

    def fake_measure(ra, dec, maj, mnr, pa):
        calls.append((ra, dec))
        return {"survey": "x", "objectR": 1, "backgroundR": 0, "netR": 1, "peakR": 1, "objectArcmin": maj or 10, "beamDiluted": True}

    monkeypatch.setattr(halpha, "measure", fake_measure)
    monkeypatch.setattr("astrodash.api_planning.time.sleep", lambda s: None)
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "h.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        item = {"ra": 10.5, "dec": 41.2, "maj": 100}
        first = client.post("/api/planning/halpha/lookup", json={"items": [item]}).json()
        assert first["found"] == {} and first["missing"] == 1
        import time as _t
        for _ in range(50):
            if calls:
                break
            _t.sleep(0.02)
        _t.sleep(0.1)
        again = client.post("/api/planning/halpha/lookup", json={"items": [item]}).json()
        assert "10.500|41.200" in again["found"] and len(calls) == 1


def _astrospheric_payload():
    """Shaped like a real response (2026-10-12), not like the API docs."""
    def v(x, color="#112233"):
        return {"ActualValue": x, "ValueColor": color}
    hours = [
        {"UTCForecastHour": "2026-10-13T01:00:00Z", "HourOffset": 1, "Cloud": v(80), "Transparency": v(29),
         "Seeing": v(0.0), "Temperature": v(282.15), "DewPoint": v(281.15), "Wind": v(4.0), "WindDirection": v(270)},
        {"UTCForecastHour": "2026-10-13T00:00:00Z", "HourOffset": 0, "Cloud": v(10), "Transparency": v(12),
         "Seeing": v(3.0), "Temperature": v(283.15), "DewPoint": v(278.15), "Wind": v(2.0),
         "GFSCloud": v(55.0), "ICONCloud": v(12.0), "Smoke": v(0.0)},
    ]
    return {
        "TimeZone": "America/Toronto", "UTCMinuteOffset": -240, "ModelTime": "2026101218",
        "HourForecastTime": "2026101222", "Latitude": 45.5, "Longitude": -73.5, "IsNBMAvailable": True,
        "APICreditCostOfCall": 130, "APICreditsRemaining": 99870, "HourlyForecast": hours,
    }


def test_astrospheric_normalize():
    from astrodash import astrospheric
    out = astrospheric.normalize(_astrospheric_payload())
    # The API's "UTC" hours are local wall time (America/Toronto, UTC−4 in October): shifted to true UTC.
    assert [h["t"] for h in out["hours"]] == ["2026-10-13T04:00:00Z", "2026-10-13T05:00:00Z"]
    first = out["hours"][0]
    first, second = out["hours"]
    assert first["cloud"] == {"value": 10, "color": "#112233"}
    assert first["temperature"]["value"] == pytest.approx(10.0)  # kelvin → °C
    assert first["dewPoint"]["value"] == pytest.approx(5.0) and "windDir" not in first
    assert "seeing" not in second and second["windDir"]["value"] == 270  # seeing 0 = no forecast under cloud
    assert out["modelTime"] == "2026-10-12T18:00:00Z"
    assert first["gfsCloud"]["value"] == 55 and first["iconCloud"]["value"] == 12 and first["smoke"]["value"] == 0
    assert out["credits"] == {"cost": 130, "remaining": 99870}
    with pytest.raises(astrospheric.ForecastError, match="only covers"):
        astrospheric.normalize({"HourlyForecast": None})
    # Unknown zone: the response's minute offset is used instead.
    odd = {**_astrospheric_payload(), "TimeZone": "Nowhere/Nothing", "UTCMinuteOffset": -300}
    assert astrospheric.normalize(odd)["hours"][0]["t"] == "2026-10-13T05:00:00Z"


def test_smoke_is_only_requested_in_wildfire_season():
    from datetime import date
    from astrodash import astrospheric
    assert "Smoke" in astrospheric.requested(date(2026, 5, 1)) and "Smoke" in astrospheric.requested(date(2026, 9, 30))
    assert "Smoke" not in astrospheric.requested(date(2026, 10, 1)) and "Smoke" not in astrospheric.requested(date(2026, 4, 30))
    assert {"Cloud", "GFSCloud", "ICONCloud", "GDPSExtended"} <= set(astrospheric.requested(date(2026, 10, 12)))


def test_astrospheric_extended_rows_follow_the_hourly_ones():
    from astrodash import astrospheric
    data = _astrospheric_payload()
    data["HourlyForecast"] += [
        {"UTCForecastHour": "2026-10-13T00:00:00Z", "HourOffset": 0, "GDPSExtended": {"ActualValue": 50, "ValueColor": "#888"}},
        {"UTCForecastHour": "2026-10-13T03:00:00Z", "HourOffset": 1, "GDPSExtended": {"ActualValue": 20.0, "ValueColor": "#333"}},
    ]
    out = astrospheric.normalize(data)
    ext = [h for h in out["hours"] if h.get("extended")]
    # 00:00 local overlaps the hourly forecast and is dropped; 03:00 local = 07:00 UTC is kept, 3 h wide.
    assert ext == [{"t": "2026-10-13T07:00:00Z", "step": 3, "extended": True, "cloud": {"value": 20.0, "color": "#333"}}]
    assert len(out["hours"]) == 3


def test_forecast_endpoint_caches_and_never_returns_the_key(data_root, tmp_path, monkeypatch):
    from astrodash import astrospheric
    cfg = Config(data_root=data_root, db_path=tmp_path / "var" / "f.db")
    app = create_app(cfg, scan_on_start=False)
    calls = []

    class Resp:
        status_code = 200
        text = ""
        def json(self):
            return _astrospheric_payload()

    def fake_post(url, json, timeout):  # noqa: A002
        calls.append(json)
        return Resp()

    with TestClient(app) as client:
        client.post("/api/sites", json={"name": "Home", "lat": 45.5, "lon": -73.5, "isDefault": True})
        monkeypatch.delenv(astrospheric.KEY_ENV, raising=False)
        assert client.get("/api/planning/forecast").json()["keySet"] is False
        monkeypatch.setenv(astrospheric.KEY_ENV, "secret-key")
        monkeypatch.setattr(astrospheric.httpx, "post", fake_post)
        first = client.get("/api/planning/forecast").json()
        second = client.get("/api/planning/forecast").json()
        assert first["cached"] is False and second["cached"] is True and len(calls) == 1
        assert calls[0]["APIKey"] == "secret-key" and "secret-key" not in str(first)
        assert len(first["forecast"]["hours"]) == 2


def test_load_env_file_does_not_override_the_real_environment(tmp_path, monkeypatch):
    from astrodash.config import load_env_file
    env = tmp_path / ".env"
    env.write_text('# comment\nASTRODASH_T1="quoted value"\nexport ASTRODASH_T2=b\nASTRODASH_T3=\nASTRODASH_T4=file\n', encoding="utf-8")
    for k in ("ASTRODASH_T1", "ASTRODASH_T2", "ASTRODASH_T3"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ASTRODASH_T4", "real")
    assert load_env_file(env) == ["ASTRODASH_T1", "ASTRODASH_T2"]
    import os
    assert os.environ["ASTRODASH_T1"] == "quoted value" and os.environ["ASTRODASH_T4"] == "real"
    assert "ASTRODASH_T3" not in os.environ
    for k in ("ASTRODASH_T1", "ASTRODASH_T2"):
        monkeypatch.delenv(k)


def test_accepted_seconds_counts_diagnostics_rejections_only_on_analysed_nights():
    rev = {"id": "r1", "sessions": [
        {"folder": "2026-01-01", "lightGroups": [
            {"filter": "H", "frames": 10, "seconds": 3000.0},
            {"filter": "O", "frames": 10, "seconds": 3000.0},
            {"filter": "S", "frames": 0, "seconds": 0.0},
        ]},
        {"folder": "2026-01-02", "lightGroups": [{"filter": "H", "frames": 4, "seconds": 1200.0}]},
    ]}
    summaries = [
        {"revisionId": "r1", "session": "2026-01-01", "finished": 1.0, "summary": {"lights": [
            {"key": "a", "filter": "Ha", "frames": 10, "kept": 8, "rejected": 2, "keptSeconds": 2400.0},
        ]}},
        {"revisionId": "other", "session": "2026-01-02", "finished": 2.0, "summary": {"lights": []}},
    ]
    per_filter, sessions = planning.accepted_seconds(rev, summaries, {"ha": "H"})
    # H: analysed night keeps 2400 of 3000, unanalysed night counts in full.
    assert per_filter["H"] == {"seconds": 4200.0, "goodSeconds": 3600.0, "analysedSeconds": 3000.0}
    # O was not in the analysis: counted in full, not analysed.
    assert per_filter["O"] == {"seconds": 3000.0, "goodSeconds": 3000.0, "analysedSeconds": 0.0}
    assert "S" not in per_filter
    assert sessions == {"2026-01-01": {"finished": 1.0, "acceptedSeconds": 5400.0, "rejected": 2}}
