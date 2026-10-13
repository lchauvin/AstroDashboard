"""Client of the Astrospheric Data API (v2): hourly cloud, transparency, seeing, wind, temperature, dew point.

The key comes from the ASTROSPHERIC_API_KEY environment variable and is added
here, server side, so it never reaches the browser and is never stored or
returned. Forecasts cover the RDPS domain only (North America, Greenland,
Iceland, Ireland, UK) and are refreshed every 6 hours upstream; the engine
caches them per site (api_planning) so page loads do not spend credits.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

KEY_ENV = "ASTROSPHERIC_API_KEY"
URL = "https://v2-api-public.astrospheric.com/api/GetForecastData"

# Response array → our field name.
VARIABLES = {
    "Cloud": "cloud",
    "Transparency": "transparency",
    "Seeing": "seeing",
    "Temperature": "temperature",
    "DewPoint": "dewPoint",
    "Wind": "wind",
    "WindDirection": "windDir",
    # Undocumented, accepted by the API (see EXTENDED). Second and third cloud opinions over the
    # same ~3½ days, for model agreement: GFS (NOAA, 20 credits) and ICON (DWD, 50 credits).
    "GFSCloud": "gfsCloud",
    "ICONCloud": "iconCloud",
    # Column smoke from the RAP model (~2 days ahead, 115 credits); its unit is not documented.
    # Transparency already includes it; it is kept to say *why* a night is hazy (wildfires).
    # Only requested in wildfire season (SMOKE_MONTHS).
    "Smoke": "smoke",
}
# Wildfire season (May–September): the only months Smoke is worth its 115 credits.
SMOKE_MONTHS = range(5, 10)
# Undocumented, but listed by the API's own "Accepted variables" error (2026-10-12): the GDPS
# long-range cloud cover the website shows as the Extended Cloud Forecast. 3-hourly, it starts
# where the hourly RDPS forecast stops (~3½ days) and reaches ~10 days; 40 credits a call.
EXTENDED = "GDPSExtended"
EXTENDED_STEP_H = 3


class ForecastError(RuntimeError):
    """A failure worth showing as-is on the Planning page."""


def key_set() -> bool:
    return bool(os.environ.get(KEY_ENV))


def requested(today: date | None = None) -> list[str]:
    """The variables to ask for: everything, minus Smoke outside wildfire season."""
    month = (today or datetime.now(timezone.utc).date()).month
    return [v for v in VARIABLES if v != "Smoke" or month in SMOKE_MONTHS] + [EXTENDED]


def fetch(lat: float, lon: float, timeout_s: float = 20.0) -> dict[str, Any]:
    """The full forecast for one location, normalized (see `normalize`)."""
    key = os.environ.get(KEY_ENV)
    if not key:
        raise ForecastError(f"Set {KEY_ENV} in .env at the project root (or the engine's environment) and restart the engine to get Astrospheric forecasts.")
    body = {"APIKey": key, "Latitude": round(lat, 4), "Longitude": round(lon, 4), "Variables": requested()}
    try:
        r = httpx.post(URL, json=body, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise ForecastError(f"Astrospheric is not reachable ({type(exc).__name__}).") from exc
    if r.status_code == 403:
        raise ForecastError(f"Astrospheric refused the key or the monthly credits are used up (check {KEY_ENV}).")
    if r.status_code == 429:
        raise ForecastError("Astrospheric is rate limiting this address; try again in a minute.")
    try:
        data = r.json()
    except ValueError as exc:
        raise ForecastError(f"Astrospheric answered {r.status_code} with something that is not JSON.") from exc
    if isinstance(data, str) and "no data" in data.casefold():
        return normalize({})
    if not isinstance(data, dict) or r.status_code != 200 or data.get("ErrorInfo"):
        info = data.get("ErrorInfo") if isinstance(data, dict) else None
        raise ForecastError(f"Astrospheric error {r.status_code}: {info or r.text[:200]}")
    return normalize(data)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _model_time(v: Any) -> str | None:
    """"2026101218" (yyyyMMddHH, what the API actually sends) → ISO 8601 UTC; ISO strings pass through."""
    if isinstance(v, str) and len(v) == 10 and v.isdigit():
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]}T{v[8:10]}:00:00Z"
    return v if isinstance(v, str) else None


def _true_utc(label: str, zone: ZoneInfo | None, minute_offset: int | None) -> str:
    """`UTCForecastHour` is the site's local wall time despite its "Z" (verified 2026-10-12: the first
    hour matched the local time of the request, and the warmest/coldest hours fell at 17:00/05:00
    labelled). Read it in the response's time zone and return true UTC, ISO 8601."""
    local = datetime.fromisoformat(label.replace("Z", "")).replace(tzinfo=None)
    if zone is not None:
        aware = local.replace(tzinfo=zone)
    else:
        aware = local.replace(tzinfo=timezone(timedelta(minutes=minute_offset or 0)))
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """One row per forecast hour, each variable as {value, color}, in the units the page shows.

    The API sends `HourlyForecast`: one entry per hour holding every variable as
    {ActualValue, ValueColor} (not the per-variable arrays its docs describe).
    Measured on a real response (2026-10-12): Cloud 0–100 %; Transparency ~10
    (best) to 29 (worst, also under cloud); Seeing 1–5, higher is better, 0 when
    overcast (= no forecast, dropped here); Temperature and DewPoint in kelvin
    (→ °C); Wind in m/s; WindDirection in degrees. GDPSExtended rows (cloud only,
    3-hourly) are appended after the hourly ones with `step: 3, extended: true`.
    Its labels are read like the others (local wall time): the two series join
    without a gap that way (hourly ends 06:00, extended starts 09:00).
    """
    rows = data.get("HourlyForecast")
    if not rows:
        # Outside the forecast domain the API answers "No Data" instead of hours.
        raise ForecastError("No Astrospheric forecast for this site: it only covers North America, Greenland, "
                            "Iceland, Ireland and the UK.")
    try:
        zone = ZoneInfo(data["TimeZone"]) if data.get("TimeZone") else None
    except (ZoneInfoNotFoundError, ValueError):
        zone = None  # fall back to the response's current offset (off by an hour across a DST change)
    hours = []
    for entry in rows:
        label = entry.get("UTCForecastHour")
        if not label:
            continue
        row: dict[str, Any] = {"t": _true_utc(label, zone, data.get("UTCMinuteOffset"))}
        for api_name, field in VARIABLES.items():
            val = entry.get(api_name) or {}
            v = _num(val.get("ActualValue"))
            if v is None or (field == "seeing" and v <= 0):
                continue
            if field in ("temperature", "dewPoint") and v > 150:
                v -= 273.15
            row[field] = {"value": round(v, 2), "color": val.get("ValueColor")}
        if len(row) > 1:  # extended-only hours carry nothing hourly
            hours.append(row)
    # The extended cloud forecast continues after the last hourly cloud value; each step covers 3 h.
    last_hourly = max((h["t"] for h in hours if "cloud" in h), default="")
    for entry in rows:
        label, val = entry.get("UTCForecastHour"), entry.get(EXTENDED) or {}
        v = _num(val.get("ActualValue"))
        if not label or v is None:
            continue
        t = _true_utc(label, zone, data.get("UTCMinuteOffset"))
        if t > last_hourly:
            hours.append({"t": t, "step": EXTENDED_STEP_H, "extended": True,
                          "cloud": {"value": round(v, 2), "color": val.get("ValueColor")}})
    return {
        "modelTime": _model_time(data.get("ModelTime")),
        "timeZone": data.get("TimeZone"),
        "utcMinuteOffset": data.get("UTCMinuteOffset"),
        "lat": data.get("Latitude"),
        "lon": data.get("Longitude"),
        "hours": sorted(hours, key=lambda h: h["t"]),
        "credits": {"cost": data.get("APICreditCostOfCall"),
                    "remaining": data.get("APICreditsRemaining", data.get("APICreditsRemainingToday"))},
    }
