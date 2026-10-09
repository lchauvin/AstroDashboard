from pathlib import Path

from astrodash.headers import parse_value, read_header
from astrodash.layout import locate_in_session_tree, parse_session_folder, parse_target_folder

from .conftest import write_fits


def test_parse_value():
    assert parse_value("'L       '") == "L"
    assert parse_value("'it''s'") == "it's"
    assert parse_value("120.0") == 120.0
    assert parse_value("9") == 9
    assert parse_value("T") is True


def test_read_fits_header(tmp_path: Path):
    p = write_fits(tmp_path / "a.fits", IMAGETYP="LIGHT", FILTER="Ha", EXPOSURE=300.0, IGNORED_KEY=1)
    h = read_header(p)
    assert h["IMAGETYP"] == "LIGHT" and h["FILTER"] == "Ha" and h["EXPOSURE"] == 300.0
    assert "IGNORED-KEY" not in h


def test_read_xisf_header(tmp_path: Path):
    xml = (
        '<?xml version="1.0"?><xisf><Image geometry="4:4:1" imageType="MasterDark">'
        '<FITSKeyword name="EXPTIME" value="300.000" comment=""/>'
        "<FITSKeyword name=\"INSTRUME\" value=\"'Cam'\" comment=\"\"/></Image></xisf>"
    ).encode()
    p = tmp_path / "m.xisf"
    p.write_bytes(b"XISF0100" + len(xml).to_bytes(4, "little") + b"\0" * 4 + xml)
    h = read_header(p)
    assert h == {"EXPTIME": 300.0, "INSTRUME": "Cam", "IMAGETYP": "MasterDark"}


def test_target_folder_and_revision():
    t = parse_target_folder("M 31 (Andromeda Galaxy) R2")
    assert (t.catalog_id, t.name, t.revision) == ("M 31", "Andromeda Galaxy", 2)
    t = parse_target_folder("V1769 Cyg (WR 134)")
    assert (t.catalog_id, t.name, t.revision) == ("V1769 Cyg", "WR 134", 1)


def test_session_names():
    assert parse_session_folder("NIGHT_2026-09-18").status == "canonical"
    assert parse_session_folder("2025-09-26").status == "legacy"
    assert parse_session_folder("SESSION_2025-08-08").status == "legacy"
    assert parse_session_folder("[GARBAGE] 2025-12-27").status == "excluded"
    assert parse_session_folder(".OLDCAMERA-SESSION_2025-08-01").status == "excluded"
    assert parse_session_folder("misc").status == "nonconforming"


def test_locate_panels_and_rejections():
    loc = locate_in_session_tree(["NIGHT_x", "M 31 Panel 2", "LIGHT", "Ha", "f.fits"])
    assert (loc.panel, loc.type_hint, loc.filter_hint) == ("2", "LIGHT", "Ha")
    loc = locate_in_session_tree(["NIGHT_x", "PANEL_3", "LIGHT", "L", "f.fits"])
    assert loc.panel == "3"
    loc = locate_in_session_tree(["NIGHT_x", "FlatWizard", "FLAT", "L", "f.fits"])
    assert (loc.panel, loc.type_hint, loc.filter_hint) == (None, "FLAT", "L")
    loc = locate_in_session_tree(["NIGHT_x", "Soul Nebula", "LIGHT", "S", "_REJECTED", "f.fits"])
    assert loc.rejected and loc.reject_parent == "NIGHT_x/Soul Nebula/LIGHT/S"
    assert locate_in_session_tree(["NIGHT_x", "Obj", "LIGHT", "S", "Accepted", "f.fits"]).copy
