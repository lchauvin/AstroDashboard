import httpx
import pytest
from fastapi.testclient import TestClient

from astrodash import telescopius
from astrodash.api import create_app
from astrodash.config import Config

# Shapes copied from real responses (2026-10-14), trimmed.
NGC7000 = {
    "main_id": "NGC 7000", "main_name": "North America Nebula",
    "ids": ["NGC 7000", "SH 2-117", "LBN 373", "C 20"], "alt_ids": ["CALDWELL 20"], "names": ["North America Nebula"],
    "types": ["eneb", "h2r"], "ra": 20.979722, "dec": 44.33, "con": "CYG", "con_name": "Cygnus",
    "visual_mag": 5, "photo_mag": 4, "major_axis": 7200, "minor_axis": 6000, "subr": 23.8,
    "url": "https://telescopius.com/deep-sky-objects/ngc-7000", "thumbnail_url": "https://telescopius.com/t.webp",
}
LIST = {"id": "345c07fa", "name": "Target List #1", "targets": [
    {"object_id": "M 81", "name": "Bode's Galaxy", "ra_hr": 9.92588139, "dec": 69.065, "size_deg": 0.356111,
     "position_angle_east": 157, "visual_mag": 6.9, "constellation_id": "UMA", "fov_width_deg": None},
    {"object_id": "nowhere", "name": "No position", "ra_hr": None, "dec": None},
]}
EVENTS = {"events": [
    {"type": "PLANET_OCCULTATION", "name": "Lunar Occultation of Mars", "objects": ["Moon", "Mars"], "deg": 1,
     "datetime": "2026-11-02T13:39:14.385Z", "visible_locally": False},
    {"type": "METEOR_SHOWER", "name": "Orionids", "datetime": "2026-10-21T09:22:45.475Z", "zhr": 20,
     "rating": "MODERATE", "moon_illumination": 75, "active_window": "2026-10-02 to 2026-11-07"},
]}


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = 200

    def __call__(self, url, params=None, headers=None, timeout=None):
        path = url.removeprefix(telescopius.BASE)
        self.calls.append((path, params or {}))
        assert headers["Authorization"] == "Key secret"
        req = httpx.Request("GET", url)
        if self.status != 200:
            return httpx.Response(self.status, text="nope", request=req)
        if path == "/targets/search":
            name = "".join(params["name"].split()).casefold()
            hit = name in {"ngc7000", "sh2-117", "northamericanebula"}
            return httpx.Response(200, json={"matched": int(hit), "page_results": [{"object": NGC7000}] if hit else []}, request=req)
        if path == "/target-lists":
            return httpx.Response(200, json={"lists": [{"id": "345c07fa", "name": "Target List #1", "url": "u"}]}, request=req)
        if path == "/target-lists/345c07fa":
            return httpx.Response(200, json=LIST, request=req)
        if path == "/events/highlights":
            return httpx.Response(200, json=EVENTS, request=req)
        return httpx.Response(404, request=req)


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setenv(telescopius.KEY_ENV, "secret")
    monkeypatch.setattr(telescopius.httpx, "get", fake)
    monkeypatch.setattr(telescopius, "MIN_INTERVAL_S", 0)
    return fake


def test_lookup_normalizes_units_and_matches_any_id(api):
    obj = telescopius.lookup("Sh2-117")
    assert obj["id"] == "NGC 7000" and obj["name"] == "North America Nebula"
    assert obj["ra"] == pytest.approx(314.6958, abs=1e-3) and obj["dec"] == 44.33
    assert obj["majArcmin"] == 120 and obj["minArcmin"] == 100 and obj["surfBr"] == 23.8
    assert "CALDWELL 20" in obj["ids"]
    assert telescopius.lookup("Nothing 1") is None


def test_lists_and_events_are_normalized(api):
    lists = telescopius.target_lists()
    assert len(lists) == 1 and [t["id"] for t in lists[0]["targets"]] == ["M 81"]  # no position → dropped
    m81 = lists[0]["targets"][0]
    assert m81["ra"] == pytest.approx(148.888, abs=1e-3) and m81["sizeArcmin"] == pytest.approx(21.37, abs=0.01)
    evs = telescopius.events(45.5, -73.5)
    assert [e["name"] for e in evs] == ["Orionids", "Lunar Occultation of Mars"]  # time order
    assert evs[1]["visibleLocally"] is False and evs[0]["zhr"] == 20


def test_errors_name_the_cause(api, monkeypatch):
    api.status = 403
    with pytest.raises(telescopius.TelescopiusError, match="refused the key"):
        telescopius.lookup("NGC 7000")
    monkeypatch.delenv(telescopius.KEY_ENV)
    with pytest.raises(telescopius.TelescopiusError, match=telescopius.KEY_ENV):
        telescopius.lookup("NGC 7000")


def test_endpoints_cache_hits_misses_and_failures(api, tmp_path):
    cfg = Config(data_root=tmp_path / "root", db_path=tmp_path / "var" / "t.db")
    app = create_app(cfg, scan_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/telescopius/status").json() == {"keySet": True}
        # First name unknown, second found; both answers cached.
        r = client.get("/api/telescopius/object", params=[("name", "Mystery 1"), ("name", "NGC7000")]).json()
        assert r["object"]["id"] == "NGC 7000" and r["matchedName"] == "NGC7000"
        n = len(api.calls)
        r = client.get("/api/telescopius/object", params=[("name", "Mystery 1"), ("name", "NGC7000")]).json()
        assert r["object"]["id"] == "NGC 7000" and len(api.calls) == n
        # A failure is reported, then not retried on every load.
        api.status = 500
        r = client.get("/api/telescopius/lists").json()
        assert r["lists"] == [] and "500" in r["error"]
        n = len(api.calls)
        client.get("/api/telescopius/lists")
        assert len(api.calls) == n
        api.status = 200
        r = client.get("/api/telescopius/lists", params={"refresh": True}).json()
        assert r["error"] is None and r["lists"][0]["targets"][0]["id"] == "M 81"
        # Events need a site.
        client.post("/api/sites", json={"name": "Home", "lat": 45.544, "lon": -73.538, "sqm": 18, "isDefault": True})
        r = client.get("/api/telescopius/events").json()
        assert [e["name"] for e in r["events"]] == ["Orionids", "Lunar Occultation of Mars"]
        assert "secret" not in str(r)


def test_endpoints_without_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv(telescopius.KEY_ENV, raising=False)
    cfg = Config(data_root=tmp_path / "root", db_path=tmp_path / "var" / "t.db")
    with TestClient(create_app(cfg, scan_on_start=False)) as client:
        r = client.get("/api/telescopius/object", params={"name": "M 31"}).json()
        assert r["keySet"] is False and r["object"] is None and telescopius.KEY_ENV in r["error"]
        assert client.get("/api/telescopius/lists").json()["lists"] == []
