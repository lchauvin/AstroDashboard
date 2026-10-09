"""Low-precision sun and moon positions (Meeus, Astronomical Algorithms ch. 25 & 47,
main terms only).

Good to a few arcminutes for the sun and ~0.3° for the moon — plenty for
"moon 62 % lit, 48° from the target" and sun-altitude thresholds. The planner
uses astronomy-engine for anything that needs real precision.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

D2R = math.pi / 180.0


def julian_day(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def _norm(deg: float) -> float:
    return deg % 360.0


def _ecl_to_eq(lon: float, lat: float, eps: float) -> tuple[float, float]:
    l, b, e = lon * D2R, lat * D2R, eps * D2R
    ra = math.atan2(math.sin(l) * math.cos(e) - math.tan(b) * math.sin(e), math.cos(l))
    dec = math.asin(math.sin(b) * math.cos(e) + math.cos(b) * math.sin(e) * math.sin(l))
    return _norm(ra / D2R), dec / D2R


def _obliquity(t: float) -> float:
    return 23.439291 - 0.0130042 * t


def sun_radec(jd: float) -> tuple[float, float]:
    t = (jd - 2451545.0) / 36525.0
    l0 = 280.46646 + 36000.76983 * t
    m = (357.52911 + 35999.05029 * t) * D2R
    c = (1.914602 - 0.004817 * t) * math.sin(m) + 0.019993 * math.sin(2 * m) + 0.000289 * math.sin(3 * m)
    return _ecl_to_eq(_norm(l0 + c), 0.0, _obliquity(t))


def moon_radec(jd: float) -> tuple[float, float]:
    t = (jd - 2451545.0) / 36525.0
    lp = 218.3164477 + 481267.88123421 * t
    d = (297.8501921 + 445267.1114034 * t) * D2R
    m = (357.5291092 + 35999.0502909 * t) * D2R
    mp = (134.9633964 + 477198.8675055 * t) * D2R
    f = (93.2720950 + 483202.0175233 * t) * D2R
    lon = (
        lp
        + 6.289 * math.sin(mp)
        + 1.274 * math.sin(2 * d - mp)
        + 0.658 * math.sin(2 * d)
        + 0.214 * math.sin(2 * mp)
        - 0.186 * math.sin(m)
        - 0.114 * math.sin(2 * f)
    )
    lat = (
        5.128 * math.sin(f)
        + 0.281 * math.sin(mp + f)
        + 0.278 * math.sin(mp - f)
        + 0.173 * math.sin(2 * d - f)
    )
    return _ecl_to_eq(_norm(lon), lat, _obliquity(t))


def separation(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Angular distance in degrees between two (RA, Dec) points given in degrees."""
    a1, d1, a2, d2 = ra1 * D2R, dec1 * D2R, ra2 * D2R, dec2 * D2R
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.acos(max(-1.0, min(1.0, cos_sep))) / D2R


def moon_illumination(jd: float) -> float:
    """Illuminated fraction 0..1 (phase angle approximated as 180° − elongation)."""
    elong = separation(*sun_radec(jd), *moon_radec(jd))
    phase_angle = 180.0 - elong
    return (1.0 + math.cos(phase_angle * D2R)) / 2.0


def altitude(ra: float, dec: float, jd: float, lat: float, lon_east: float) -> float:
    gmst = 280.46061837 + 360.98564736629 * (jd - 2451545.0)
    ha = (_norm(gmst + lon_east - ra)) * D2R
    phi, delta = lat * D2R, dec * D2R
    sin_alt = math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(ha)
    return math.asin(max(-1.0, min(1.0, sin_alt))) / D2R


def moon_altitude(jd: float, lat: float, lon_east: float) -> float:
    return altitude(*moon_radec(jd), jd, lat, lon_east)


def sun_altitude(jd: float, lat: float, lon_east: float) -> float:
    return altitude(*sun_radec(jd), jd, lat, lon_east)
