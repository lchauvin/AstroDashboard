"""Synthetic data-root fixtures. Tests never touch G:\\."""

from __future__ import annotations

from pathlib import Path

import pytest


def _card(key: str, value) -> str:
    if isinstance(value, bool):
        v = "T" if value else "F"
        text = f"{key:<8}= {v:>20}"
    elif isinstance(value, (int, float)):
        text = f"{key:<8}= {value!r:>20}"
    else:
        text = f"{key:<8}= '{value}'"
    return text.ljust(80)[:80]


def write_fits(path: Path, **header) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = [_card("SIMPLE", True), _card("BITPIX", 16), _card("NAXIS", 2), _card("NAXIS1", 4), _card("NAXIS2", 4)]
    cards += [_card(k.replace("_", "-"), v) for k, v in header.items()]
    cards.append("END".ljust(80))
    raw = "".join(cards).encode("ascii")
    raw += b" " * (-len(raw) % 2880)
    data = b"\0" * 32
    data += b"\0" * (-len(data) % 2880)
    path.write_bytes(raw + data)
    return path


def light(**kw):
    base = dict(
        IMAGETYP="LIGHT", EXPOSURE=300.0, GAIN=9, OFFSET=100, READOUTM="Linearity HDR",
        XBINNING=1, SET_TEMP=-10.0, INSTRUME="Cam", TELESCOP="Scope", FOCALLEN=250.0,
        FOCPOS=1000, SITELAT=45.5, SITELONG=-73.5, RA=10.0, DEC=40.0,
    )
    base.update(kw)
    return base


def flat(**kw):
    base = dict(IMAGETYP="FLAT", EXPOSURE=2.0, GAIN=69, OFFSET=1, READOUTM="Full Resolution", XBINNING=1, FOCPOS=1000)
    base.update(kw)
    return base


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "Astro"
    # Canonical target: two nights; night 2 has no flats for O.
    t = root / "IC 1848 (Soul Nebula)" / ".SessionData"
    for i in range(3):
        write_fits(t / "NIGHT_2026-02-01" / "LIGHT" / "H" / f"h{i}.fits",
                   **light(FILTER="H", DATE_LOC=f"2026-02-01T22:0{i}:00", DATE_OBS=f"2026-02-02T03:0{i}:00"))
    write_fits(t / "NIGHT_2026-02-01" / "FLAT" / "H" / "f0.fits", **flat(FILTER="H"))
    # Session bias at the flats' Full Resolution settings
    write_fits(t / "NIGHT_2026-02-01" / "BIAS" / "b0.fits", IMAGETYP="BIAS", EXPOSURE=0.0, GAIN=69, OFFSET=1,
               READOUTM="Full Resolution", XBINNING=1)
    # Night 2: HDR lights at gain 69 / offset 1 — gain/offset are ignored in HDR
    for i in range(2):
        write_fits(t / "NIGHT_2026-02-02" / "LIGHT" / "O" / f"o{i}.fits",
                   **light(FILTER="OIII", GAIN=69, OFFSET=1, DATE_LOC=f"2026-02-03T01:0{i}:00"))
    # Full Resolution flats with no bias at their gain/offset: calibrated with the HDR library bias (intended)
    write_fits(t / "NIGHT_2026-02-02" / "FLAT" / "S" / "f0.fits", **flat(FILTER="S", GAIN=80, OFFSET=30))
    # A Full Resolution light group: HDR masters must not match it
    write_fits(t / "NIGHT_2026-02-02" / "LIGHT" / "S" / "s0.fits",
               **light(FILTER="S", READOUTM="Full Resolution", GAIN=80, OFFSET=30, DATE_LOC="2026-02-03T02:00:00"))
    # astro-eval style: a copy in _REJECTED of a frame still in the parent folder, plus Accepted copies
    write_fits(t / "NIGHT_2026-02-01" / "LIGHT" / "H" / "_REJECTED" / "h2.fits", **light(FILTER="H"))
    write_fits(t / "NIGHT_2026-02-01" / "LIGHT" / "H" / "Accepted" / "h0.fits", **light(FILTER="H"))
    # Legacy flagged session
    write_fits(t / "[GARBAGE] 2025-12-27" / "LIGHT" / "H" / "g.fits", **light(FILTER="H"))

    # Legacy layout: year level, NINA object folder, mosaic panels, FlatWizard, quarantine
    m = root / "M 31 (Andromeda Galaxy)" / "2026"
    for p in (1, 2):
        write_fits(m / ".SessionData" / "NIGHT_2026-08-20" / f"M 31 Panel {p}" / "LIGHT" / "L" / f"l{p}.fits",
                   **light(FILTER="L", EXPOSURE=60.0))
    write_fits(m / ".SessionData" / "NIGHT_2026-08-20" / "FlatWizard" / "FLAT" / "L" / "f.fits",
               **flat(FILTER="L", READOUTM="Linearity HDR"))   # flats in the wrong readout mode
    write_fits(m / ".MispointedFrames_Quarantine" / "NIGHT_2026-08-20" / "M 31 Panel 1" / "LIGHT" / "L" / "q.fits",
               **light(FILTER="L", EXPOSURE=60.0))
    (m / ".Finished").mkdir(parents=True)

    # Revision 2 of the same object
    write_fits(root / "M 31 (Andromeda Galaxy) R2" / ".SessionData" / "NIGHT_2027-09-01" / "PANEL_1" / "LIGHT" / "L" / "a.fits",
               **light(FILTER="L", EXPOSURE=60.0))

    # Dark library: master names without gain info + raw darks that provide it
    lib = root / ".Calibration" / "2026-09-17"
    write_fits(lib / "DARK" / "Dark" / "d0.fits", IMAGETYP="DARK", EXPOSURE=300.0, GAIN=9, OFFSET=100,
               READOUTM="Linearity HDR", SET_TEMP=-10.0, INSTRUME="Cam")
    write_fits(lib / "Masters" / "-10.0C" / "MasterDark_-10C_300s.fits", IMAGETYP="Dark", EXPTIME=300.0, INSTRUME="Cam")
    write_fits(lib / "BIAS" / "Dark" / "b0.fits", IMAGETYP="BIAS", EXPOSURE=0.25, GAIN=69, OFFSET=1,
               READOUTM="Linearity HDR", SET_TEMP=-10.0, INSTRUME="Cam")
    write_fits(lib / "Masters" / "-10.0C" / "masterBias_-10.fits", IMAGETYP="Master Bias", EXPTIME=0.0, INSTRUME="Cam")
    return root
