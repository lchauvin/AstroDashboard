"""Phase 3 detectors (PLAN.md §4.2), computed from a stored astro-eval result plus the
FITS headers the Library already indexed.

They are cheap and run when a result is read, so they improve without re-measuring.
Every detector returns *findings* — episodes with a time range, the frames involved
and a plain-language explanation — and per-frame flags for the table and timeline.

Thresholds are module constants, named after what they mean.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable

from .. import sky

# --- thresholds -------------------------------------------------------------------
CLOUD_TRANSPARENCY = 0.60      # stellar flux below 60 % of the session's typical frame
CLOUD_STARS = 0.50             # or fewer than half the usual stars
OBSTRUCTION_REGION = 0.50      # worst 1/16 of the frame transmits < 50 % of the clear part …
OBSTRUCTION_MIN_TRANSPARENCY = 0.60   # … while the frame as a whole is still clear (not a cloud)
TWILIGHT_SUN_ALT = -18.0
SKY_RISE = 1.12                # background ≥ 12 % above the filter's typical frame
GRADIENT_RISE = 1.5            # gradient ≥ 1.5 × the filter's typical frame
DEW_MARGIN_C = 2.0             # ambient − dew point
FWHM_RISE = 1.15               # FWHM ≥ 15 % above the filter's typical frame
FOCUS_TEMP_DRIFT_C = 1.5       # focuser temperature change since the last focus move
TRACKING_ECC = 0.50            # absolute floor …
TRACKING_ECC_ABOVE = 0.12      # … and this much above the filter's typical eccentricity
TRACKING_CONSISTENCY = 0.70    # stars elongated in the same direction (not random seeing)
MISPOINT_MIN_PX = 150.0        # registration offset vs the reference frame
MISPOINT_FACTOR = 4.0          # … and ≥ 4 × the session's typical (dither) offset
ROTATION_CHANGE_DEG = 2.0      # camera rotation differs from the group (flips excluded)
EPISODE_GAP_MIN = 20.0         # flagged frames closer than this belong to one episode


def _t(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso)[:26])
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _median(vals: Iterable[float | None]) -> float | None:
    v = [x for x in vals if x is not None]
    return statistics.median(v) if v else None


def _clock(t: datetime) -> str:
    return t.strftime("%H:%M") + " UTC"


class _Frame:
    """One light frame with everything the detectors look at."""

    def __init__(self, row: dict[str, Any], group: dict[str, Any], header: dict[str, Any], site):
        self.row = row
        self.path = row["path"]
        self.filter = group["filter"]
        self.group = group["key"]
        self.t = _t(row.get("obs_time"))
        self.kept = not row["rejected"]
        self.transparency = _num(row.get("transparency"))
        self.region = _num(row.get("region_transparency_min"))
        self.stars = _num(row.get("n_stars"))
        self.bg = _num(row.get("background_median"))
        self.gradient = _num(row.get("background_gradient"))
        self.fwhm = _num(row.get("fwhm_median"))
        self.ecc = _num(row.get("eccentricity_median"))
        self.consistency = _num(row.get("elongation_consistency"))
        self.sun = _num(row.get("sunAlt"))
        self.n_matched = _num(row.get("n_matched"))
        tr = row.get("ref_transform")
        self.offset = self.rotation = None
        if tr and len(tr) == 6:
            a, b, c, d, tx, ty = tr
            # Shift of the image *centre*: the transform rotates about the corner, so a meridian
            # flip (180°) alone would otherwise look like a shift the size of the image diagonal.
            w, h = (_num(header.get("NAXIS1")), _num(header.get("NAXIS2"))) if header else (None, None)
            cx, cy = (w / 2, h / 2) if w and h else (0.0, 0.0)
            self.offset = math.hypot(a * cx + b * cy + tx - cx, c * cx + d * cy + ty - cy)
            self.rotation = math.degrees(math.atan2(c, a))
        self.alt = _num(header.get("CENTALT")) if header else None
        if self.alt is None:
            self.alt = _num(row.get("altitude_deg"))
        self.az = _num(header.get("CENTAZ")) if header else None
        amb, dew = _num(header.get("AMBTEMP")), _num(header.get("DEWPOINT"))
        self.dew_margin = amb - dew if amb is not None and dew is not None else None
        self.foctemp = _num(header.get("FOCTEMP")) if header else None
        self.focpos = _num(header.get("FOCPOS", header.get("FOCUSPOS"))) if header else None
        self.pier = header.get("PIERSIDE") if header else None
        self.moon_alt = self.moon_sep = self.moon_illum = None
        ra, dec = (_num(header.get("RA")), _num(header.get("DEC"))) if header else (None, None)
        if self.t and site:
            jd = sky.julian_day(self.t)
            mra, mdec = sky.moon_radec(jd)
            self.moon_alt = round(sky.altitude(mra, mdec, jd, site[0], site[1]), 1)
            self.moon_illum = round(sky.moon_illumination(jd), 2)
            if ra is not None and dec is not None:
                self.moon_sep = round(sky.separation(ra, dec, mra, mdec))
        # filled once group medians are known
        self.stars_rel = self.bg_rel = self.grad_rel = self.fwhm_rel = None
        self.ecc_typical = None


def _episodes(frames: list[_Frame], flagged: set[str]) -> list[list[_Frame]]:
    """Consecutive flagged frames (all filters, in time order), split on gaps > EPISODE_GAP_MIN."""
    out: list[list[_Frame]] = []
    for f in frames:
        if f.path not in flagged or f.t is None:
            continue
        if out and (f.t - out[-1][-1].t).total_seconds() <= EPISODE_GAP_MIN * 60:
            out[-1].append(f)
        else:
            out.append([f])
    return out


def _finding(code: str, level: str, title: str, text: str, frames: list[_Frame]) -> dict[str, Any]:
    times = [f.t for f in frames if f.t]
    return {
        "code": code, "level": level, "title": title, "text": text,
        "from": min(times).isoformat() if times else None,
        "to": max(times).isoformat() if times else None,
        "frames": [f.path for f in frames],
        "filters": sorted({f.filter for f in frames}),
    }


def _brighter(rise: float | None) -> str:
    if rise is None:
        return "rising"
    return f"{rise:.0f}× brighter" if rise >= 2 else f"up to {rise - 1:.0%} brighter"


def _level(ep: list[_Frame]) -> str:
    """A single odd frame is a note; a run of them is a warning."""
    return "warn" if len(ep) > 1 else "info"


def _n(n: int) -> str:
    return f"{n} frame" if n == 1 else f"{n} frames"


def _range(lo: float, hi: float, unit: str = "°") -> str:
    return f"{lo:.0f}{unit}" if round(lo) == round(hi) else f"{lo:.0f}–{hi:.0f}{unit}"


def _span(ep: list[_Frame]) -> str:
    return f"{_clock(ep[0].t)}–{_clock(ep[-1].t)}" if ep[0].t and ep[-1].t else ""


def analyse(result: dict[str, Any], headers: dict[str, dict[str, Any]], site) -> dict[str, Any]:
    """Findings, per-frame flags and per-frame context (moon, dew…) for one stored result."""
    frames: list[_Frame] = []
    for g in result.get("lights", []):
        for row in g["frames"]:
            frames.append(_Frame(row, g, headers.get(row["path"], {}), site))
    frames.sort(key=lambda f: f.t or datetime.max.replace(tzinfo=timezone.utc))

    # Per-group typical values, from frames that are themselves clear.
    by_group: dict[str, list[_Frame]] = {}
    for f in frames:
        by_group.setdefault(f.group, []).append(f)
    for fs in by_group.values():
        base = [f for f in fs if f.kept] or fs
        med = {k: _median(getattr(f, k) for f in base) for k in ("stars", "bg", "gradient", "fwhm", "ecc")}
        for f in fs:
            f.ecc_typical = med["ecc"]
            if med["stars"] and f.stars is not None:
                f.stars_rel = f.stars / med["stars"]
            if med["bg"] and f.bg is not None:
                f.bg_rel = f.bg / med["bg"]
            if med["gradient"] and f.gradient is not None:
                f.grad_rel = f.gradient / med["gradient"]
            if med["fwhm"] and f.fwhm is not None:
                f.fwhm_rel = f.fwhm / med["fwhm"]

    flags: dict[str, list[str]] = {f.path: [] for f in frames}
    findings: list[dict[str, Any]] = []

    def flag(code: str, fs: Iterable[_Frame]) -> set[str]:
        s = {f.path for f in fs}
        for p in s:
            flags[p].append(code)
        return s

    # --- obstruction: one region dark while the frame overall is clear --------------
    obstructed = flag("obstruction", [
        f for f in frames
        if f.region is not None and f.region < OBSTRUCTION_REGION
        and (f.transparency is None or f.transparency >= OBSTRUCTION_MIN_TRANSPARENCY)
    ])
    for ep in _episodes(frames, obstructed):
        azs = [f.az for f in ep if f.az is not None]
        alts = [f.alt for f in ep if f.alt is not None]
        where = ""
        if azs and alts:
            where = f" with the telescope at az {_range(min(azs), max(azs))}, alt {_range(min(alts), max(alts))}"
        findings.append(_finding(
            "obstruction", _level(ep), "Partial obstruction",
            f"{_n(len(ep))}{where}: part of the field lost most of its starlight while the rest "
            "stayed clear — typically a tree, roof or the dew shield at low altitude.", ep))

    # --- twilight: background or gradient rising while the sun is above −18° ----------
    twilight = [f for f in frames if f.sun is not None and f.sun > TWILIGHT_SUN_ALT]
    creep = flag("twilight", [
        f for f in twilight
        if (f.bg_rel is not None and f.bg_rel >= SKY_RISE) or (f.grad_rel is not None and f.grad_rel >= GRADIENT_RISE)
    ])
    timed = [f.t for f in frames if f.t]
    middle = timed[len(timed) // 2] if timed else None
    for ep in _episodes(frames, creep):
        dawn = bool(middle and ep[0].t and ep[0].t > middle)      # second half of the night
        rise = max((f.bg_rel for f in ep if f.bg_rel is not None), default=None)
        findings.append(_finding(
            "twilight", "warn", "Dawn creep" if dawn else "Dusk",
            f"{_n(len(ep))} with the sun up to {max(f.sun for f in ep):.0f}° and the sky background "
            f"{_brighter(rise)} — expect a gradient; drop them if the target has enough data.", ep))
    quiet = [f for f in twilight if f.path not in creep]
    if quiet:
        findings.append(_finding(
            "twilight-ok", "info", "Frames in twilight",
            f"{_n(len(quiet))} taken with the sun above −18°, without measurable sky brightening.", quiet))

    # --- clouds: the whole frame dims ------------------------------------------------
    cloudy = flag("clouds", [
        f for f in frames
        if f.path not in obstructed and f.path not in creep and (
            (f.transparency is not None and f.transparency < CLOUD_TRANSPARENCY)
            or (f.stars_rel is not None and f.stars_rel < CLOUD_STARS)
        )
    ])
    for ep in _episodes(frames, cloudy):
        worst = min((f.transparency for f in ep if f.transparency is not None), default=None)
        findings.append(_finding(
            "clouds", _level(ep), "Clouds or haze",
            f"{_n(len(ep))}: stellar flux and star count dropped across the whole field"
            + (f" (worst transparency {worst:.0%})" if worst is not None else "") + ".", ep))

    # --- moon gradient --------------------------------------------------------------------
    moonlit = flag("moon", [
        f for f in frames
        if f.moon_alt is not None and f.moon_alt > 0 and f.path not in creep and f.path not in cloudy
        and ((f.bg_rel is not None and f.bg_rel >= SKY_RISE) or (f.grad_rel is not None and f.grad_rel >= GRADIENT_RISE))
    ])
    for ep in _episodes(frames, moonlit):
        illum = max(f.moon_illum or 0 for f in ep)
        seps = [f.moon_sep for f in ep if f.moon_sep is not None]
        findings.append(_finding(
            "moon", _level(ep), "Moon gradient",
            f"{_n(len(ep))}: the moon was up ({illum:.0%} lit"
            + (f", {_range(min(seps), max(seps))} from the target" if seps else "")
            + ") and the background or its gradient rose.", ep))

    # --- dew ---------------------------------------------------------------------------
    damp = [f for f in frames if f.dew_margin is not None and f.dew_margin < DEW_MARGIN_C]
    soft = {f.path for f in damp if f.fwhm_rel is not None and f.fwhm_rel >= FWHM_RISE}
    dew = flag("dew", [f for f in damp if f.path in soft])
    for ep in _episodes(frames, dew):
        m = min(f.dew_margin for f in ep)
        findings.append(_finding(
            "dew", "warn", "Possible dew",
            f"{_n(len(ep))}: only {m:.1f} °C between air and dew point while stars grew "
            f"≥ {FWHM_RISE - 1:.0%} wider — check the dew heater.", ep))
    if damp and not dew:
        findings.append(_finding(
            "dew-risk", "info", "Dew risk",
            f"{_n(len(damp))} with less than {DEW_MARGIN_C:.0f} °C between air and dew point, but star sizes held.", damp))

    # --- focus drift: focuser temperature moved since the last focus change ---------------
    drift: list[_Frame] = []
    for fs in by_group.values():
        ref_temp = None
        last_pos = None
        for f in sorted((x for x in fs if x.t), key=lambda x: x.t):
            if f.focpos is not None and f.focpos != last_pos:
                last_pos, ref_temp = f.focpos, f.foctemp          # (auto)focus moved: new reference
            if (ref_temp is not None and f.foctemp is not None and abs(f.foctemp - ref_temp) >= FOCUS_TEMP_DRIFT_C
                    and f.fwhm_rel is not None and f.fwhm_rel >= FWHM_RISE):
                drift.append(f)
    drift_set = flag("focus", drift)
    for ep in _episodes(frames, drift_set):
        findings.append(_finding(
            "focus", _level(ep), "Focus drift",
            f"{_n(len(ep))}: the focuser cooled/warmed by ≥ {FOCUS_TEMP_DRIFT_C:g} °C since the last focus "
            f"move and stars grew ≥ {FWHM_RISE - 1:.0%} wider — refocus more often (temperature trigger).", ep))

    # --- tracking: elongated stars all pointing the same way ---------------------------------
    trailing = flag("tracking", [
        f for f in frames
        if f.ecc is not None and f.consistency is not None and f.consistency >= TRACKING_CONSISTENCY
        and f.ecc >= max(TRACKING_ECC, (f.ecc_typical or 0) + TRACKING_ECC_ABOVE)
    ])
    runs = [ep for ep in _episodes(frames, trailing) if len(ep) > 1]
    for ep in runs:
        findings.append(_finding(
            "tracking", "warn", "Tracking / guiding",
            f"{_n(len(ep))}: stars clearly more elongated than usual tonight (eccentricity up to "
            f"{max(f.ecc for f in ep):.2f}), all in the same direction — guiding, wind or balance rather than seeing.", ep))
    single = [f for ep in _episodes(frames, trailing) if len(ep) == 1 for f in ep]
    if single:
        findings.append(_finding(
            "tracking-isolated", "info", "Isolated elongated frames",
            f"{_n(len(single))} with elongated stars, each isolated — a gust or a guiding hiccup.", single))

    # --- mispointing and rotation (registration against the group's reference) --------------
    mis: list[_Frame] = []
    rot: list[_Frame] = []
    for fs in by_group.values():
        offsets = [f.offset for f in fs if f.offset is not None]
        typical = _median(offsets) or 0.0
        limit = max(MISPOINT_MIN_PX, MISPOINT_FACTOR * typical)
        matched = _median(f.n_matched for f in fs if f.n_matched)
        # Rotation modulo 180°: meridian flips are expected and must not count as a change.
        folded = {f.path: (f.rotation + 90) % 180 - 90 for f in fs if f.rotation is not None}
        rot_ref = _median(folded.values())
        for f in fs:
            lost = (matched and f.n_matched is not None and f.n_matched < 0.3 * matched
                    and f.stars_rel is not None and f.stars_rel >= 0.7)
            if (f.offset is not None and f.offset > limit) or lost:
                mis.append(f)
            if f.path in folded and rot_ref is not None:
                if abs((folded[f.path] - rot_ref + 90) % 180 - 90) >= ROTATION_CHANGE_DEG:
                    rot.append(f)
    mis_set = flag("mispointed", mis)
    for ep in _episodes(frames, mis_set):
        offs = [f.offset for f in ep if f.offset is not None]
        findings.append(_finding(
            "mispointed", _level(ep), "Mispointed frames",
            f"{_n(len(ep))} shifted"
            + (f" by {_range(min(offs), max(offs), ' px')}" if offs else "") + " from the rest of the night (or barely overlapping it) — "
            "a failed plate-solve or centering; integration crops it away or rejects it.", ep))
    rot_set = flag("rotation", rot)
    if rot_set:
        findings.append(_finding(
            "rotation", "warn", "Camera rotation changed",
            f"{_n(len(rot_set))} are rotated ≥ {ROTATION_CHANGE_DEG:g}° from the rest of their filter (meridian flips "
            "excluded): flats taken before the change may not match.", [f for f in frames if f.path in rot_set]))

    # --- events for the timeline -----------------------------------------------------------
    events: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    for f in frames:
        if f.t is None:
            continue
        if f.pier and last.get("pier") and f.pier != last["pier"]:
            events.append({"kind": "flip", "time": f.t.isoformat(), "label": f"Meridian flip ({last['pier']} → {f.pier})"})
        if f.focpos is not None and last.get("pos", {}).get(f.filter) not in (None, f.focpos):
            events.append({"kind": "focus", "time": f.t.isoformat(), "label": f"Autofocus / focus move ({f.filter})"})
        last["pier"] = f.pier or last.get("pier")
        if f.focpos is not None:
            last.setdefault("pos", {})[f.filter] = f.focpos

    # Flat ↔ light comparison measured during the run (pixel level).
    for fm in result.get("flatMatch", []) or []:
        if fm.get("verdict") in ("warn", "bad"):
            findings.append({
                "code": "flat-mismatch", "level": "warn", "title": f"Flats vs lights · {fm['filter']}",
                "text": " ".join(fm.get("notes", [])), "from": None, "to": None, "frames": [], "filters": [fm["filter"]],
            })

    order = {"bad": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda x: (order.get(x["level"], 3), x["from"] or ""))
    context = {
        f.path: {
            "moonAlt": f.moon_alt, "moonSep": f.moon_sep, "moonIllum": f.moon_illum, "dewMargin": f.dew_margin,
            "focTemp": f.foctemp, "focPos": f.focpos, "pier": f.pier, "az": f.az, "alt": f.alt,
            "offsetPx": round(f.offset, 1) if f.offset is not None else None,
            "rotation": round(f.rotation, 2) if f.rotation is not None else None,
        }
        for f in frames
    }
    return {"findings": findings, "flags": {p: v for p, v in flags.items() if v}, "events": events, "context": context}


def horizon_samples(result: dict[str, Any], headers: dict[str, dict[str, Any]], site) -> list[tuple[float, float, bool]]:
    """(az, alt, obstructed) for every frame with a pointing; used to learn the horizon."""
    out = []
    a = analyse(result, headers, site)
    for path, ctx in a["context"].items():
        if ctx["az"] is None or ctx["alt"] is None:
            continue
        flags = a["flags"].get(path, [])
        if "clouds" in flags:
            continue                     # a cloudy frame says nothing about the horizon
        out.append((ctx["az"], ctx["alt"], "obstruction" in flags))
    return out


def learn_horizon(samples: list[tuple[float, float, bool]], bin_deg: int = 10) -> list[dict[str, Any]]:
    """Per azimuth bin: highest altitude seen obstructed and lowest seen clear."""
    bins = []
    for start in range(0, 360, bin_deg):
        inside = [(alt, obs) for az, alt, obs in samples if start <= az % 360 < start + bin_deg]
        blocked = [alt for alt, obs in inside if obs]
        clear = [alt for alt, obs in inside if not obs]
        bins.append({
            "az": start, "width": bin_deg,
            "blockedMaxAlt": round(max(blocked), 1) if blocked else None,
            "clearMinAlt": round(min(clear), 1) if clear else None,
            "blocked": len(blocked), "clear": len(clear),
            # Two or more obstructed frames make a bin's horizon credible.
            "horizon": round(max(blocked), 1) if len(blocked) >= 2 else None,
        })
    return bins
