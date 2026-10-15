"""NINA's per-device `/equipment/{device}/info` payloads → display cards.

Only connected devices become cards. Each card has a short `status` line and a list of
facts; facts marked `advanced` are hidden unless the page is in Advanced mode. Field names
have moved between plugin versions, so every lookup goes through `_pick` (case-insensitive,
several spellings, tolerant of absence) and an odd payload never breaks the card.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

LABELS = {
    "camera": "Camera", "mount": "Mount", "focuser": "Focuser", "filterwheel": "Filter wheel",
    "rotator": "Rotator", "guider": "Guider (NINA)", "flatdevice": "Flat panel", "weather": "Weather",
    "safetymonitor": "Safety monitor", "dome": "Dome", "switch": "Switch",
}


def _pick(payload: Any, *keys: str) -> Any:
    if not isinstance(payload, dict):
        return None
    lowered = {str(k).lower(): v for k, v in payload.items()}
    for key in keys:
        v = lowered.get(key.lower())
        if v not in (None, ""):
            return v
    return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fact(facts: list, label: str, value: Any, unit: str = "", digits: int = 1, *, advanced: bool = False,
          level: str | None = None) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, (int, float)):
        text = f"{value:.{digits}f}{unit}" if isinstance(value, float) or digits else f"{value}{unit}"
    else:
        text = f"{value}{unit}"
    facts.append({"label": label, "value": text, "advanced": advanced, **({"level": level} if level else {})})


def _hours(h: float) -> str:
    total = int(round(h * 60))
    return f"{total // 60} h {total % 60:02d} min" if total >= 60 else f"{total} min"


# NINA's clock minus ours, set by the caller before building cards: its timestamps are on the observatory PC's clock.
CLOCK = {"offset": 0.0}


def _seconds_until(stamp: Any, offset: float | None = None) -> float | None:
    """Seconds from now until a NINA timestamp, corrected for the difference between the two PCs' clocks."""
    if not isinstance(stamp, str) or stamp.startswith("0001"):
        return None
    try:
        t = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.astimezone()  # NINA writes local time without an offset
    off = CLOCK["offset"] if offset is None else offset
    return (t - datetime.now(timezone.utc)).total_seconds() - off


def _camera(p: dict, facts: list) -> tuple[str, str | None]:
    temp, setp = _num(_pick(p, "Temperature")), _num(_pick(p, "TemperatureSetPoint", "SetTemperature"))
    cooler, power = _pick(p, "CoolerOn"), _num(_pick(p, "CoolerPower"))
    level = None
    if temp is not None and setp is not None and cooler and abs(temp - setp) > 1.0:
        level = "warn"
    _fact(facts, "Sensor", temp, " °C", level=level)
    if cooler:
        _fact(facts, "Set point", setp, " °C", 0)
        _fact(facts, "Cooler", power, " %", 0, level="warn" if power is not None and power >= 95 else None)
    else:
        _fact(facts, "Cooler", "off")
    _fact(facts, "Gain", _num(_pick(p, "Gain")), "", 0, advanced=True)
    _fact(facts, "Offset", _num(_pick(p, "Offset")), "", 0, advanced=True)
    binx = _pick(p, "BinX")
    if binx:
        _fact(facts, "Binning", f"{binx}×{_pick(p, 'BinY') or binx}", advanced=True)
    modes, mode = _pick(p, "ReadoutModes"), _pick(p, "ReadoutMode")
    if isinstance(modes, list) and isinstance(mode, int) and 0 <= mode < len(modes):
        _fact(facts, "Readout", modes[mode], advanced=True)
    _fact(facts, "Dew heater", _pick(p, "DewHeaterOn"), advanced=True)
    battery = _num(_pick(p, "Battery"))
    if battery is not None and battery >= 0:
        _fact(facts, "Battery", battery, " %", 0, advanced=True)
    _fact(facts, "Last download", _num(_pick(p, "LastDownloadTime")), " s", 1, advanced=True)
    state = str(_pick(p, "CameraState") or ("Exposing" if _pick(p, "IsExposing") else "Idle"))
    left = _seconds_until(_pick(p, "ExposureEndTime"))
    if _pick(p, "IsExposing") and left is not None and left > 0:
        state = f"Exposing · {left:.0f} s left"
    return state, level


def _mount(p: dict, facts: list) -> tuple[str, str | None]:
    alt = _num(_pick(p, "Altitude"))
    _fact(facts, "Altitude", alt, "°", 0, level="warn" if alt is not None and alt < 20 else None)
    _fact(facts, "Azimuth", _num(_pick(p, "Azimuth")), "°", 0)
    pier = _pick(p, "SideOfPier")
    if isinstance(pier, str):
        _fact(facts, "Pier side", pier.replace("pier", "").strip() or None)
    flip = _num(_pick(p, "TimeToMeridianFlip"))
    if flip is not None and 0 < flip < 24:
        _fact(facts, "Meridian flip", f"in {_hours(flip)}", level="info" if flip < 0.25 else None)
    coords = _pick(p, "Coordinates")
    _fact(facts, "RA", _pick(p, "RightAscensionString") or _pick(coords, "RAString"), advanced=True)
    _fact(facts, "Dec", _pick(p, "DeclinationString") or _pick(coords, "DecString"), advanced=True)
    _fact(facts, "Sidereal time", _pick(p, "SiderealTimeString"), advanced=True)
    rate = _pick(p, "TrackingRate")
    _fact(facts, "Tracking rate", _pick(rate, "TrackingMode") if isinstance(rate, dict) else rate, advanced=True)
    if _pick(p, "AtPark"):
        return "Parked", "warn"
    if _pick(p, "Slewing"):
        return "Slewing", None
    if _pick(p, "TrackingEnabled") is False:
        return "Not tracking", "warn"
    return "Tracking", None


def _focuser(p: dict, facts: list) -> tuple[str, str | None]:
    pos = _num(_pick(p, "Position"))
    _fact(facts, "Position", int(pos) if pos is not None else None, "", 0)
    _fact(facts, "Temperature", _num(_pick(p, "Temperature")), " °C")
    _fact(facts, "Step size", _num(_pick(p, "StepSize")), " µm", 1, advanced=True)
    _fact(facts, "Temp. compensation", _pick(p, "TempComp"), advanced=True)
    return ("Moving" if _pick(p, "IsMoving") else "Settling" if _pick(p, "IsSettling") else "Idle"), None


def _filterwheel(p: dict, facts: list) -> tuple[str, str | None]:
    sel = _pick(p, "SelectedFilter")
    name = _pick(sel, "Name") if isinstance(sel, dict) else sel
    _fact(facts, "Filter", name)
    avail = _pick(p, "AvailableFilters")
    if isinstance(avail, list):
        _fact(facts, "Filters", ", ".join(str(_pick(f, "Name") or f) for f in avail), advanced=True)
    return ("Moving" if _pick(p, "IsMoving") else "Ready"), None


def _rotator(p: dict, facts: list) -> tuple[str, str | None]:
    _fact(facts, "Angle", _num(_pick(p, "Position")), "°")
    _fact(facts, "Mechanical", _num(_pick(p, "MechanicalPosition")), "°", advanced=True)
    _fact(facts, "Reversed", _pick(p, "Reverse"), advanced=True)
    return ("Moving" if _pick(p, "IsMoving") else "Idle"), None


def _guider(p: dict, facts: list) -> tuple[str, str | None]:
    rms = _pick(p, "RMSError")
    total = _pick(rms, "Total") if isinstance(rms, dict) else None
    arcsec = _num(_pick(total, "Arcseconds") if isinstance(total, dict) else total)
    _fact(facts, "RMS (NINA)", arcsec, "″", 2, advanced=True)
    _fact(facts, "Pixel scale", _num(_pick(p, "PixelScale")), "″/px", 2, advanced=True)
    return str(_pick(p, "State") or "—"), None


def _flat(p: dict, facts: list) -> tuple[str, str | None]:
    _fact(facts, "Cover", _pick(p, "CoverState"))
    _fact(facts, "Light", "on" if _pick(p, "LightOn") else "off")
    _fact(facts, "Brightness", _num(_pick(p, "Brightness")), "", 0, advanced=True)
    return str(_pick(p, "CoverState") or "Ready"), None


def _weather(p: dict, facts: list) -> tuple[str, str | None]:
    temp, dew = _num(_pick(p, "Temperature")), _num(_pick(p, "DewPoint"))
    _fact(facts, "Clouds", _num(_pick(p, "CloudCover")), " %", 0)
    _fact(facts, "Temperature", temp, " °C")
    if temp is not None and dew is not None:
        _fact(facts, "Dew margin", temp - dew, " °C", level="warn" if temp - dew < 2.5 else None)
    _fact(facts, "Humidity", _num(_pick(p, "Humidity")), " %", 0)
    _fact(facts, "Wind", _num(_pick(p, "WindSpeed")), " m/s")
    _fact(facts, "Gusts", _num(_pick(p, "WindGust")), " m/s", advanced=True)
    _fact(facts, "Sky quality", _num(_pick(p, "SkyQuality")), " mag/″²", 2, advanced=True)
    _fact(facts, "Sky temperature", _num(_pick(p, "SkyTemperature")), " °C", advanced=True)
    _fact(facts, "Pressure", _num(_pick(p, "Pressure")), " hPa", 0, advanced=True)
    return "Reporting", None


def _safety(p: dict, facts: list) -> tuple[str, str | None]:
    safe = _pick(p, "IsSafe")
    return ("Safe", None) if safe else ("UNSAFE", "bad")


def _dome(p: dict, facts: list) -> tuple[str, str | None]:
    _fact(facts, "Shutter", _pick(p, "ShutterStatus"))
    _fact(facts, "Azimuth", _num(_pick(p, "Azimuth")), "°", 0)
    _fact(facts, "Following", _pick(p, "IsFollowing"), advanced=True)
    return ("Slewing" if _pick(p, "Slewing") else "Parked" if _pick(p, "AtPark") else "Ready"), None


def _switch(p: dict, facts: list) -> tuple[str, str | None]:
    for group in ("WritableSwitches", "ReadonlySwitches", "Gauges"):
        for sw in _pick(p, group) or []:
            if isinstance(sw, dict) and _pick(sw, "Name"):
                v = _num(_pick(sw, "Value"))
                _fact(facts, str(_pick(sw, "Name")), v, "", 1 if v is not None and v % 1 else 0, advanced=True)
    return "Ready", None


BUILDERS = {
    "camera": _camera, "mount": _mount, "focuser": _focuser, "filterwheel": _filterwheel, "rotator": _rotator,
    "guider": _guider, "flatdevice": _flat, "weather": _weather, "safetymonitor": _safety, "dome": _dome,
    "switch": _switch,
}


def cards(equipment: dict[str, Any]) -> list[dict]:
    out = []
    for key, payload in equipment.items():
        if not isinstance(payload, dict) or _pick(payload, "Connected") is not True:
            continue
        facts: list[dict] = []
        try:
            status, level = BUILDERS[key](payload, facts)
        except Exception:  # noqa: BLE001 — an odd payload still shows the device as connected
            status, level = "Connected", None
        out.append({
            "key": key,
            "label": LABELS.get(key, key),
            "name": _pick(payload, "DisplayName", "Name", "DeviceId"),
            "status": status,
            "level": level,
            "facts": facts,
        })
    return out


def connected_keys(equipment: dict[str, Any]) -> set[str]:
    return {k for k, p in equipment.items() if isinstance(p, dict) and _pick(p, "Connected") is True}
