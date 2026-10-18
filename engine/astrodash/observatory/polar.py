"""Polar alignment from guiding, without touching the mount: PHD2's Dec corrections record the drift they cancel.

A misaligned polar axis makes the pointing drift in declination. Guiding hides the drift by pushing
back, so over a window the drift the mount *would* have made is

    drift = Δ(Dec residual) + Σ (signed Dec pulse × calibrated Dec rate)

summed over undisturbed stretches (a pulse at step i moves the residual measured at step i+1).
Dithers, settling, flips and star loss break a window into segments; nothing across them counts.

Geometry (small errors, northern hemisphere). With the polar axis Δalt too high and Δaz east of the
pole, the pointing's Dec rate at hour angle H is

    dδ/dt = K · (Δaz·cos φ·cos H − Δalt·sin H)      K = 0.2625 ″/min per arcmin

It does not depend on the declination: one target or several, it is the same curve. Near the
meridian the drift measures azimuth, six hours out it measures altitude (classic drift alignment).
So:

* one window gives |drift| / K, a **lower bound** on the total error (the part this H can see);
* the windows of a night, fitted against H, give both components;
* a window that leaves the fitted curve, twice in a row, means the mount *moved* (a leg settling,
  a bump): that starts a new epoch, and the fit restarts from it.

Assumptions: "North" pulses move the pointing to celestial north (ASCOM's contract). Should the Dec
sense flip with the pier side, both hypotheses are fitted and the better one kept, but then the
directions are withheld (which side is right cannot be told). The calibrated Dec rate scales every
figure: a calibration 10 % off is a drift 10 % off. Refraction adds a Dec drift of its own at low
altitude, flagged below 30°.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from . import guiding
from .equipment import _num, _pick

K = 60 * 2 * math.pi / 1436.07  # ″/min of Dec drift per arcmin of polar error, at most (sidereal rate)
WINDOW_S = 300.0
MIN_CLEAN_S = 120.0
MIN_GAP_S = 15.0  # consecutive steps further apart than this (or 3× the cadence) split a segment
FIT_MIN_WINDOWS, FIT_MIN_SPAN_H = 6, 1.5
SHIFT_MIN, SHIFT_SIGMA = 0.5, 3.0  # ″/min (≈ 2′) and noise multiples a window must leave the curve by
SIGMA_FLOOR = 0.15  # ″/min: no window is trusted better than this
LOW_ALT = 30.0
KEEP_H = 14.0
NOW_WINDOWS = 3


def pulse_sign(samples: list[guiding.Sample]) -> int:
    """+1 when a positive Dec residual draws North pulses, −1 when South; 0 without pulses.

    Learned from the data rather than from PHD2's sign conventions, which depend on parity and version.
    """
    acc = sum(s.dec_px * s.dec_dir * s.dec_ms for s in samples)
    return 1 if acc > 0 else -1 if acc < 0 else 0


def segments(samples: list[guiding.Sample], dist: guiding.Disturbances | None) -> list[list[guiding.Sample]]:
    if not samples:
        return []
    dts = sorted(b.t - a.t for a, b in zip(samples, samples[1:]))
    gap = max(MIN_GAP_S, 3 * dts[len(dts) // 2]) if dts else MIN_GAP_S
    out: list[list[guiding.Sample]] = []
    cur: list[guiding.Sample] = []
    for s in samples:
        if dist and dist.excluded(s.t):
            cur = []
            continue
        if cur and s.t - cur[-1].t > gap:
            cur = []
        if not cur:
            out.append(cur)
        cur.append(s)
    return [seg for seg in out if len(seg) >= 2]


def window_rate(samples: list[guiding.Sample], dist: guiding.Disturbances | None, rate_px_s: float, scale: float,
                sign: int) -> dict | None:
    """The pointing's Dec drift over a window, ″/min, + = north. None with too little clean guiding."""
    drift_px, seconds, n = 0.0, 0.0, 0
    s = sign or 1
    for seg in segments(samples, dist):
        pulses = sum(p.dec_dir * p.dec_ms for p in seg[:-1]) * rate_px_s / 1000
        drift_px += (seg[-1].dec_px - seg[0].dec_px) + s * pulses
        seconds += seg[-1].t - seg[0].t
        n += len(seg)
    if seconds < MIN_CLEAN_S:
        return None
    # A positive residual (for s = +1) is corrected northwards, so it is the pointing lying south.
    return {"rate": -s * drift_px * scale / (seconds / 60), "clean": round(seconds), "n": n}


def mount_context(mount: dict | None) -> dict:
    """Hour angle (h), Dec, altitude, pier side and latitude from NINA's mount info; missing fields are None."""
    m = mount or {}
    if not m.get("Connected"):
        return {}
    lst, ra = _num(_pick(m, "SiderealTime")), _num(_pick(m, "RightAscension"))
    ha = ((lst - ra + 12) % 24) - 12 if lst is not None and ra is not None else None
    pier = _pick(m, "SideOfPier")
    pier = pier.replace("pier", "").strip() if isinstance(pier, str) else None
    return {"ha": ha, "dec": _num(_pick(m, "Declination")), "alt": _num(_pick(m, "Altitude")),
            "pier": pier if pier in ("East", "West") else None, "lat": _num(_pick(m, "SiteLatitude")),
            "moving": bool(_pick(m, "Slewing")) or _pick(m, "TrackingEnabled") is False}


def _solve(rows: list[tuple[float, float, float]]) -> tuple[float, float, float, list[list[float]]] | None:
    """Least squares r = a·cos H + b·sin H over (H in hours, r, weight). → a, b, SSR, (XᵀWX)⁻¹."""
    sxx = sxy = syy = bx = by = 0.0
    for h, r, w in rows:
        c, s = math.cos(h * math.pi / 12), math.sin(h * math.pi / 12)
        sxx += w * c * c
        sxy += w * c * s
        syy += w * s * s
        bx += w * c * r
        by += w * s * r
    det = sxx * syy - sxy * sxy
    if det <= 1e-9 * max(1.0, sxx * syy):
        return None
    inv = [[syy / det, -sxy / det], [-sxy / det, sxx / det]]
    a, b = inv[0][0] * bx + inv[0][1] * by, inv[1][0] * bx + inv[1][1] * by
    ssr = sum(w * (r - a * math.cos(h * math.pi / 12) - b * math.sin(h * math.pi / 12)) ** 2 for h, r, w in rows)
    return a, b, ssr, inv


def fit(windows: list[dict]) -> dict:
    """Both components from windows spread in hour angle. {"ok": False, "reason": …} when they are not."""
    pts = [w for w in windows if w.get("ha") is not None]
    if len(pts) < FIT_MIN_WINDOWS:
        return {"ok": False, "reason": f"{len(pts)} of {FIT_MIN_WINDOWS} windows with a known hour angle"}
    span = max(w["ha"] for w in pts) - min(w["ha"] for w in pts)
    if span < FIT_MIN_SPAN_H:
        return {"ok": False, "reason": f"hour angle spans {span:.1f} h of the {FIT_MIN_SPAN_H:g} h needed to split altitude from azimuth"}
    both = {w["pier"] for w in pts} >= {"East", "West"}
    best = None
    for flipped in (False, True) if both else (False,):
        rows = [(w["ha"], -w["rate"] if flipped and w["pier"] == "West" else w["rate"], w["clean"] / WINDOW_S) for w in pts]
        sol = _solve(rows)
        if sol and (best is None or sol[2] < best[1][2]):
            best = (flipped, sol)
    if best is None:
        return {"ok": False, "reason": "the hour angles do not separate the two components"}
    flipped, (a, b, ssr, inv) = best
    wsum = sum(w["clean"] / WINDOW_S for w in pts)
    sigma = max(SIGMA_FLOOR, math.sqrt(ssr / max(1e-9, wsum) * len(pts) / (len(pts) - 2)))
    lats = [w["lat"] for w in pts if w.get("lat") is not None]
    lat = lats[-1] if lats else None
    cos_lat = math.cos(math.radians(lat)) if lat is not None else 1.0
    alt, az = -b / K, a / (K * cos_lat)
    alt_se, az_se = sigma * math.sqrt(inv[1][1]) / K, sigma * math.sqrt(inv[0][0]) / (K * cos_lat)
    return {"ok": True, "alt": round(alt, 1), "az": round(az, 1), "altSe": round(alt_se, 1), "azSe": round(az_se, 1),
            "total": round(math.hypot(alt, az * cos_lat), 1), "sigma": round(sigma, 2), "a": a, "b": b, "spanH": round(span, 1),
            "n": len(pts), "pierFlipped": flipped, "latKnown": lat is not None,
            "directions": not flipped and (lat is None or lat > 0)}


def predict(f: dict, w: dict) -> float | None:
    if not f.get("ok") or w.get("ha") is None:
        return None
    h = w["ha"] * math.pi / 12
    r = f["a"] * math.cos(h) + f["b"] * math.sin(h)
    return -r if f["pierFlipped"] and w.get("pier") == "West" else r


def advice(f: dict) -> list[str]:
    """Which way to turn the knobs, only for components clearly above their noise."""
    if not f.get("ok") or not f["directions"]:
        return []
    out = []
    if abs(f["alt"]) > 2 * f["altSe"] and abs(f["alt"]) >= 1:
        out.append(f"{'lower' if f['alt'] > 0 else 'raise'} the altitude by about {abs(f['alt']):.0f}′")
    if abs(f["az"]) > 2 * f["azSe"] and abs(f["az"]) >= 1:
        out.append(f"turn the azimuth {'west' if f['az'] > 0 else 'east'} by about {abs(f['az']):.0f}′")
    return out


@dataclass
class PolarTracker:
    """Closes one window every WINDOW_S of wall clock, keeps tonight's windows, fits and watches for shifts."""

    store: Any  # get_json / set_json

    def __post_init__(self) -> None:
        saved = self.store.get_json("polar_log") or {}
        cutoff = time.time() - KEEP_H * 3600
        self.windows: list[dict] = [w for w in saved.get("windows", []) if w["t"] >= cutoff]
        self.shifts: list[dict] = [s for s in saved.get("shifts", []) if s["t"] >= cutoff]
        self.epoch = self.windows[-1]["epoch"] if self.windows else 0
        self.last_end = 0.0
        self.status: str | None = None  # why the last window was not kept

    def save(self) -> None:
        self.store.set_json("polar_log", {"windows": self.windows, "shifts": self.shifts})

    def update(self, buffer: guiding.GuideBuffer, dist: guiding.Disturbances, calibration: dict | None,
               mount: dict | None, now: float | None = None) -> dict | None:
        """Call often; does work once per window. Returns a new shift when one is confirmed."""
        now = now or time.time()
        end = math.floor(now / WINDOW_S) * WINDOW_S
        if end <= self.last_end:
            return None
        self.last_end, start = end, end - WINDOW_S
        rate_px_s = _num((calibration or {}).get("yRate"))
        if not rate_px_s or not buffer.scale:
            self.status = "PHD2's Dec calibration rate is unknown" if not rate_px_s else "the guide pixel scale is unknown"
            return None
        ctx = mount_context(mount)
        if ctx.get("moving"):
            self.status = "the mount was slewing or not tracking"
            return None
        est = window_rate(buffer.window(start, end), dist, rate_px_s, buffer.scale, pulse_sign(list(buffer.samples)))
        if est is None:
            self.status = "less than two minutes of clean guiding in the window"
            return None
        self.status = None
        ha = ctx.get("ha")
        w = {"t": start + WINDOW_S / 2, "rate": round(est["rate"], 3), "clean": est["clean"], "n": est["n"],
             "ha": round(ha - WINDOW_S / 2 / 3600, 3) if ha is not None else None, "dec": ctx.get("dec"), "alt": ctx.get("alt"),
             "pier": ctx.get("pier"), "lat": ctx.get("lat"), "epoch": self.epoch}
        self.windows = [x for x in self.windows if x["t"] >= now - KEEP_H * 3600] + [w]
        shift = self._check_shift()
        self.save()
        return shift

    def _reference(self, ref: list[dict], w: dict) -> tuple[float, float] | None:
        """(prediction, noise) for window w from the earlier windows of its epoch."""
        f = fit(ref)
        if f["ok"] and (p := predict(f, w)) is not None:
            return p, f["sigma"]
        near = [x for x in ref if w["t"] - x["t"] <= 2400][-4:]
        if len(near) < 3:
            return None
        mean = sum(x["rate"] for x in near) / len(near)
        sd = math.sqrt(sum((x["rate"] - mean) ** 2 for x in near) / (len(near) - 1))
        return mean, max(SIGMA_FLOOR, sd)

    def _check_shift(self) -> dict | None:
        cur = [w for w in self.windows if w["epoch"] == self.epoch]
        if len(cur) < 5:
            return None
        ref, (w1, w2) = cur[:-2], cur[-2:]
        got = self._reference(ref, w1), self._reference(ref, w2)
        if None in got:
            return None
        devs = [w["rate"] - p for w, (p, _) in zip((w1, w2), got)]
        limits = [max(SHIFT_MIN, SHIFT_SIGMA * sd) for _, sd in got]
        if not all(abs(d) > lim for d, lim in zip(devs, limits)) or (devs[0] > 0) != (devs[1] > 0):
            return None
        self.epoch += 1
        w1["epoch"] = w2["epoch"] = self.epoch
        delta = (devs[0] + devs[1]) / 2
        shift = {"t": w1["t"] - WINDOW_S / 2, "before": round(got[0][0], 2), "after": round((w1["rate"] + w2["rate"]) / 2, 2),
                 "delta": round(delta, 2), "deltaArcmin": round(abs(delta) / K, 1), "epoch": self.epoch}
        self.shifts.append(shift)
        return shift

    def summary(self, now: float | None = None) -> dict:
        now = now or time.time()
        tonight = [w for w in self.windows if w["t"] >= now - KEEP_H * 3600]
        cur = [w for w in tonight if w["epoch"] == self.epoch]
        recent = [w for w in cur if now - w["t"] <= (NOW_WINDOWS + 1) * WINDOW_S][-NOW_WINDOWS:]
        rate = None
        if recent:
            rate = sum(w["rate"] * w["clean"] for w in recent) / sum(w["clean"] for w in recent)
        f = fit(cur)
        caveats = []
        last = tonight[-1] if tonight else None
        if last and last.get("alt") is not None and last["alt"] < LOW_ALT:
            caveats.append(f"Altitude {last['alt']:.0f}°: refraction adds a Dec drift of its own below {LOW_ALT:.0f}°.")
        if f.get("ok") and f["pierFlipped"]:
            caveats.append("The Dec sense seems to flip with the pier side: sizes are fine, directions are withheld.")
        if f.get("ok") and not f["latKnown"]:
            caveats.append("NINA reports no site latitude: azimuth is given on the sky (× cos latitude).")
        if f.get("ok") and f["latKnown"] and not f["directions"] and not f["pierFlipped"]:
            caveats.append("Southern hemisphere: directions are withheld (the signs are worked out for the north).")
        series = {"t": [round(w["t"] - now) for w in tonight], "rate": [w["rate"] for w in tonight],
                  "model": [round(p, 3) if (p := predict(f, w)) is not None and w["epoch"] == self.epoch else None for w in tonight]}
        return {
            "now": {"rate": round(rate, 2), "minArcmin": round(abs(rate) / K, 1), "windows": len(recent),
                    "ha": recent[-1]["ha"]} if rate is not None else None,
            "fit": {k: v for k, v in f.items() if k not in ("a", "b")},
            "advice": advice(f),
            "shifts": self.shifts[-5:],
            "windows": len(tonight),
            "status": self.status,
            "caveats": caveats,
            "series": series,
            "windowS": WINDOW_S,
        }
