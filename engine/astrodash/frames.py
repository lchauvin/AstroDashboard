"""Normalize a scanned file + its header into a Frame the library can reason about."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .layout import (
    MASTER_BIAS_RE,
    MASTER_DARK_RE,
    TEMP_DIR_RE,
    FrameLocation,
    locate_in_session_tree,
    parse_session_folder,
)
from .scanner import FrameFile

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = _FRACTION_RE.sub(r"\1", value.strip())
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_kind(imagetyp: Any, hint: str | None, filename: str) -> str | None:
    # A master's file name wins: PixInsight does not always write "Master …" in IMAGETYP.
    lowered = filename.casefold()
    for prefix, kind in (("masterdark", "MASTERDARK"), ("masterbias", "MASTERBIAS"), ("masterflat", "MASTERFLAT")):
        if lowered.startswith(prefix):
            return kind
    text = (_str(imagetyp) or "").upper().replace("_", " ").replace("-", " ")
    compact = text.replace(" ", "")
    if compact.startswith("MASTER"):
        rest = compact[len("MASTER"):]
        for kind in ("DARKFLAT", "FLATDARK", "DARK", "BIAS", "FLAT"):
            if rest.startswith(kind):
                return "MASTER" + ("DARKFLAT" if kind == "FLATDARK" else kind)
    if compact in {"LIGHT", "LIGHTFRAME", "OBJECT"}:
        return "LIGHT"
    if compact in {"FLAT", "FLATFIELD", "FLATFRAME"}:
        return "FLAT"
    if compact in {"DARK", "DARKFRAME"}:
        return "DARK"
    if compact in {"BIAS", "BIASFRAME", "OFFSET"}:
        return "BIAS"
    if compact in {"DARKFLAT", "FLATDARK"}:
        return "DARKFLAT"
    if hint in {"DARKFLAT", "FLATDARK"}:
        return "DARKFLAT"
    return hint


def canonical_filter(raw: Any, aliases: dict[str, str]) -> str | None:
    text = _str(raw)
    if text is None:
        return None
    return aliases.get(text.casefold(), text)


@dataclass
class Frame:
    path: str
    area: str
    target_folder: str | None
    rel_parts: list[str]
    size: int
    kind: str | None
    filter: str | None
    exposure: float | None
    gain: float | None
    offset: float | None
    readout: str | None
    binning: int | None
    set_temp: float | None
    instrume: str | None
    telescop: str | None
    focallen: float | None
    focpos: float | None
    time_utc: datetime | None
    time_local: datetime | None
    night: date | None
    status: str                       # ok | rejected | quarantined | copy | ignored | excluded | unreadable
    location: FrameLocation | None
    session_folder: str | None
    session_status: str | None        # canonical | legacy | excluded | nonconforming
    gain_ignored: bool = False        # readout mode ignores gain/offset (gain/offset set to None)
    time_end_utc: datetime | None = None  # masters: DATE-END = last input frame
    header: dict[str, Any] = field(default_factory=dict)

    @property
    def settings_key(self) -> tuple:
        return (self.exposure, self.gain, self.offset, self.readout, self.binning, self.set_temp, self.instrume)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def night_of(local: datetime | None, utc: datetime | None, lon_east: float | None) -> date | None:
    """A night runs local noon to local noon and is named after its evening."""
    if local is None and utc is not None:
        offset_h = (lon_east or 0.0) / 15.0
        local = utc + timedelta(hours=offset_h)
    if local is None:
        return None
    return (local - timedelta(hours=12)).date()


def build_frame(ff: FrameFile, aliases: dict[str, str], gain_ignored=lambda readout: False) -> Frame:
    h = ff.header or {}
    location = None
    session_folder = None
    session_status = None
    if ff.area in ("session", "quarantine"):
        location = locate_in_session_tree(ff.rel_parts)
        if location:
            session_folder = location.session
            session_status = parse_session_folder(location.session).status

    filename = ff.rel_parts[-1]
    kind = normalize_kind(h.get("IMAGETYP"), location.type_hint if location else None, filename)
    filt = canonical_filter(h.get("FILTER"), aliases)
    if filt is None and location and location.filter_hint:
        filt = canonical_filter(location.filter_hint, aliases)

    exposure = _num(h.get("EXPOSURE"))
    if exposure is None:
        exposure = _num(h.get("EXPTIME"))
    set_temp = _num(h.get("SET-TEMP"))
    if set_temp is None and ff.area == "library":
        set_temp = _library_temp(ff.rel_parts, filename)
    if set_temp is None:
        ccd = _num(h.get("CCD-TEMP"))
        set_temp = round(ccd) if ccd is not None else None
    if exposure is None and kind == "MASTERDARK":
        m = MASTER_DARK_RE.search(filename)
        exposure = float(m["exp"]) if m else None

    time_utc = parse_time(h.get("DATE-OBS"))
    if time_utc is not None and time_utc.tzinfo is None:
        time_utc = time_utc.replace(tzinfo=timezone.utc)
    time_local = parse_time(h.get("DATE-LOC"))
    lon = _num(h.get("SITELONG"))
    binning = _num(h.get("XBINNING"))

    readout = _str(h.get("READOUTM"))
    # In readout modes that ignore gain/offset the header values are noise for grouping
    # and matching; they stay available in `header`.
    ignores_gain = gain_ignored(readout)

    status = "ok"
    if ff.error and not ff.header:
        status = "unreadable"
    if ff.area == "quarantine":
        status = "quarantined"
    if location:
        if location.ignored:
            status = "ignored"
        elif location.copy:
            status = "copy"
        elif location.rejected and status == "ok":
            status = "rejected"
        if session_status in ("excluded", "nonconforming"):
            status = "excluded"

    return Frame(
        path=ff.path,
        area=ff.area,
        target_folder=ff.target_folder,
        rel_parts=ff.rel_parts,
        size=ff.size,
        kind=kind,
        filter=filt,
        exposure=round(exposure, 6) if exposure is not None else None,
        gain=None if ignores_gain else _num(h.get("GAIN")),
        offset=None if ignores_gain else _num(h.get("OFFSET")),
        readout=readout,
        gain_ignored=ignores_gain,
        time_end_utc=_utc(parse_time(h.get("DATE-END"))),
        binning=int(binning) if binning is not None else None,
        set_temp=set_temp,
        instrume=_str(h.get("INSTRUME")),
        telescop=_str(h.get("TELESCOP")),
        focallen=_num(h.get("FOCALLEN")),
        focpos=_num(h.get("FOCPOS")) if h.get("FOCPOS") is not None else _num(h.get("FOCUSPOS")),
        time_utc=time_utc,
        time_local=time_local,
        night=night_of(time_local, time_utc, lon),
        status=status,
        location=location,
        session_folder=session_folder,
        session_status=session_status,
        header=h,
    )


def _library_temp(rel_parts: list[str], filename: str) -> float | None:
    for part in reversed(rel_parts[:-1]):
        m = TEMP_DIR_RE.match(part)
        if m:
            return float(m.group(1))
    m = MASTER_DARK_RE.search(filename) or MASTER_BIAS_RE.search(filename)
    return float(m["temp"]) if m else None
