from pathlib import Path

from astrodash.config import DEFAULT_FILTER_ALIASES
from astrodash.live_flats import sequence_filters, tonight_flats

from .conftest import write_fits

ALIASES = dict(DEFAULT_FILTER_ALIASES)
TONIGHT = "2026-11-18"


def step(name, **info):
    return {"name": name, "info": info}


def flat_fits(path: Path, filt: str, readout: str | None, when="2026-11-19T05:30:00"):
    hdr = {"IMAGETYP": "FLAT", "FILTER": filt, "DATE-LOC": when, "EXPOSURE": 1.0}
    if readout:
        hdr["READOUTM"] = readout
    return write_fits(path, **{k.replace("-", "_"): v for k, v in hdr.items()})


def test_sequence_filters_are_the_light_exposures_only():
    steps = [
        step("Smart Exposure", Filter="Ha", ImageType="LIGHT", ExposureTime=300),
        step("Smart Exposure", Filter="OIII", ImageType="LIGHT", ExposureTime=300),
        step("Switch Filter", Filter="SII"),
        step("Trained Flat Exposure", Filter="Ha", ImageType="FLAT", ExposureTime=1),
        step("Take Exposure", Filter="Ha", ImageType="LIGHT", ExposureTime=300),
    ]
    assert sequence_filters(steps, ALIASES) == ["H", "O"]


def test_levels_ok_warn_bad_and_waiting_flats(tmp_path):
    root = tmp_path / "NGC 281 (Pacman Nebula) R2" / ".SessionData"
    night = root / f"NIGHT_{TONIGHT}" / "FLAT"
    flat_fits(night / "H" / "h1.fits", "Ha", "Full Resolution")
    flat_fits(night / "H" / "h2.fits", "Ha", "Full Resolution")
    flat_fits(night / "O" / "o1.fits", "OIII", "Linearity HDR")
    flat_fits(night / "O" / "o2.fits", "OIII", "Full Resolution")
    flat_fits(night / "L" / "old.fits", "L", "Full Resolution", when="2026-11-10T05:00:00")  # another night
    waiting = flat_fits(tmp_path / ".Download" / TONIGHT / "s.fits", "SII", "Full Resolution")
    write_fits(root / f"NIGHT_{TONIGHT}" / "LIGHT" / "H" / "l.fits", IMAGETYP="LIGHT", FILTER="Ha")

    steps = [step("Smart Exposure", Filter=f, ImageType="LIGHT", ExposureTime=300) for f in ("Ha", "OIII", "SII", "L")]
    out = tonight_flats(tonight=TONIGHT, steps=steps, light_filters=[], session_roots=[str(root)],
                        waiting=[str(waiting)], aliases=ALIASES, want_readout="Full Resolution")
    rows = {r["filter"]: r for r in out["filters"]}
    assert out["source"] == "sequence"
    assert rows["H"]["level"] == "ok" and rows["H"]["frames"] == 2
    assert rows["O"]["level"] == "warn" and rows["O"]["wrong"] == ["Linearity HDR"]
    assert rows["S"]["level"] == "ok"  # still in the download folder
    assert rows["L"]["level"] == "bad"  # only an older night's flats
    assert out["other"] == []


def test_without_a_sequence_tonights_light_filters_stand_in(tmp_path):
    out = tonight_flats(tonight=TONIGHT, steps=[], light_filters=["H", "H", "O"], session_roots=[str(tmp_path)],
                        waiting=[], aliases=ALIASES, want_readout="Full Resolution")
    assert out["source"] == "lights"
    assert [(r["filter"], r["level"]) for r in out["filters"]] == [("H", "bad"), ("O", "bad")]


def test_a_re_copied_flat_in_the_download_folder_counts_once(tmp_path):
    root = tmp_path / "T (Name)" / ".SessionData"
    ingested = flat_fits(root / f"NIGHT_{TONIGHT}" / "FLAT" / "H" / "f1.fits", "Ha", "Full Resolution")
    copy = tmp_path / ".Download" / TONIGHT / "f1.fits"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(ingested.read_bytes())
    steps = [step("Smart Exposure", Filter="Ha", ImageType="LIGHT", ExposureTime=300)]
    out = tonight_flats(tonight=TONIGHT, steps=steps, light_filters=[], session_roots=[str(root)], waiting=[str(copy)],
                        aliases=ALIASES, want_readout="Full Resolution")
    assert out["filters"][0]["frames"] == 1


def test_missing_readout_header_is_a_warning(tmp_path):
    f = flat_fits(tmp_path / "x.fits", "Ha", None)
    steps = [step("Smart Exposure", Filter="Ha", ImageType="LIGHT", ExposureTime=300)]
    out = tonight_flats(tonight=TONIGHT, steps=steps, light_filters=[], session_roots=[], waiting=[str(f)],
                        aliases=ALIASES, want_readout="Full Resolution")
    assert out["filters"][0]["level"] == "warn" and out["filters"][0]["unknownReadout"] == 1
