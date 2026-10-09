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
