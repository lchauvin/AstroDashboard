"""Guiding telemetry: PHD2 guide steps → arcsec samples, windowed statistics and a per-axis diagnosis.

* RMS is taken **about zero**, not about the mean: a persistent offset (drift, flexure,
  polar misalignment) is real error and a standard deviation would hide it.
* Disturbances (dither + settle, calibration, star lost, meridian flip, autofocus, pauses)
  are kept as time intervals and the samples inside them are left out of the statistics —
  otherwise a dither looks like bad guiding and gets "fixed" with a parameter change.
* RMS alone cannot tell an axis that rings from a night of bad seeing: 0.9″ of each looks
  the same and needs opposite fixes. So each axis also gets the *shape* of its error
  (lag-1 autocorrelation, signed mean, idle and reversal fractions), and `diagnose` turns
  that into a verdict with the textbook fix. The AI advisor gets numbers and verdict.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

# Seconds after each disturbance during which samples are still excluded.
GUARDS = {"dither": 30.0, "settling": 5.0, "calibrating": 5.0, "star lost": 60.0, "paused": 5.0,
          "meridian flip": 300.0, "autofocus": 10.0, "parameter change": 20.0}


@dataclass(frozen=True)
class Sample:
    t: float
    frame: int
    ra: float  # arcsec
    dec: float
    ra_px: float
    dec_px: float
    snr: float
    mass: float
    hfd: float  # px
    ra_ms: float
    dec_ms: float
    ra_dir: int  # +1 East / North, −1 West / South, 0 no pulse
    dec_dir: int
    ra_limited: bool
    dec_limited: bool


@dataclass
class Interval:
    reason: str
    start: float
    end: float = math.inf


@dataclass
class Disturbances:
    """Open/close intervals by name, plus fixed-length marks. Wall-clock seconds."""

    items: list[Interval] = field(default_factory=list)
    open: dict[str, Interval] = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=200))
    """(t, label) markers for the chart: dithers, star lost, flips, parameter changes."""

    def begin(self, reason: str, t: float | None = None) -> None:
        if reason not in self.open:
            iv = Interval(reason, t or time.time())
            self.open[reason] = iv
            self.items.append(iv)

    def end(self, reason: str, t: float | None = None) -> None:
        iv = self.open.pop(reason, None)
        if iv:
            iv.end = (t or time.time()) + GUARDS.get(reason, 5.0)

    def mark(self, reason: str, t: float | None = None, label: str | None = None) -> None:
        t = t or time.time()
        self.items.append(Interval(reason, t, t + GUARDS.get(reason, 10.0)))
        self.events.append((t, label or reason))

    def excluded(self, t: float) -> bool:
        return any(iv.start <= t < iv.end for iv in self.items)

    def active(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        return sorted({iv.reason for iv in self.items if iv.start <= now < iv.end})

    def prune(self, keep_s: float = 3600) -> None:
        cutoff = time.time() - keep_s
        self.items = [iv for iv in self.items if iv.end >= cutoff]

    def clean_seconds(self, start: float, end: float) -> float:
        spans = sorted((max(iv.start, start), min(iv.end, end)) for iv in self.items if iv.start < end and start < iv.end)
        covered, cur_lo, cur_hi = 0.0, None, None
        for lo, hi in spans:
            if cur_hi is None or lo > cur_hi:
                if cur_hi is not None:
                    covered += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            else:
                cur_hi = max(cur_hi, hi)
        if cur_hi is not None:
            covered += cur_hi - cur_lo
        return max(0.0, (end - start) - covered)


def _direction(v) -> int:
    if not isinstance(v, str):
        return 0
    v = v.strip().lower()
    return 1 if v in ("east", "north") else -1 if v in ("west", "south") else 0


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class GuideBuffer:
    def __init__(self, keep_s: float = 3600.0) -> None:
        self.keep_s = keep_s
        self.samples: deque[Sample] = deque(maxlen=20_000)
        self.scale: float | None = None

    def add(self, step: dict, t: float | None = None) -> Sample | None:
        """A PHD2 GuideStep → Sample. None until the pixel scale is known (arcsec are meaningless without it)."""
        if not self.scale:
            return None
        t = t or time.time()
        ra_px, dec_px = _f(step.get("RADistanceRaw")), _f(step.get("DECDistanceRaw"))
        s = Sample(
            t=t, frame=int(_f(step.get("Frame"))), ra=ra_px * self.scale, dec=dec_px * self.scale, ra_px=ra_px, dec_px=dec_px,
            snr=_f(step.get("SNR")), mass=_f(step.get("StarMass")), hfd=_f(step.get("HFD")),
            ra_ms=_f(step.get("RADuration")), dec_ms=_f(step.get("DECDuration")),
            ra_dir=_direction(step.get("RADirection")), dec_dir=_direction(step.get("DECDirection")),
            ra_limited=bool(step.get("RALimited")), dec_limited=bool(step.get("DecLimited")),
        )
        self.samples.append(s)
        while self.samples and self.samples[0].t < t - self.keep_s:
            self.samples.popleft()
        return s

    def window(self, start: float, end: float, dist: Disturbances | None = None) -> list[Sample]:
        return [s for s in self.samples if start <= s.t < end and not (dist and dist.excluded(s.t))]

    def series(self, seconds: float, dist: Disturbances) -> dict:
        now = time.time()
        pts = [s for s in self.samples if s.t >= now - seconds]
        return {
            "t": [round(s.t - now, 1) for s in pts],
            "ra": [round(s.ra, 3) for s in pts],
            "dec": [round(s.dec, 3) for s in pts],
            "raMs": [round(s.ra_ms * (s.ra_dir or 1)) for s in pts],
            "decMs": [round(s.dec_ms * (s.dec_dir or 1)) for s in pts],
            "snr": [round(s.snr, 1) for s in pts],
            "excluded": [dist.excluded(s.t) for s in pts],
            "events": [{"t": round(t - now, 1), "label": label} for t, label in dist.events if t >= now - seconds],
        }


def _rms(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.0


def _median(v: list[float]) -> float:
    if not v:
        return 0.0
    o = sorted(v)
    m = len(o) // 2
    return o[m] if len(o) % 2 else 0.5 * (o[m - 1] + o[m])


def _lag1(v: list[float]) -> float:
    """Lag-1 autocorrelation about the mean (the offset is reported separately)."""
    n = len(v)
    if n < 3:
        return 0.0
    mean = sum(v) / n
    d = [x - mean for x in v]
    den = sum(x * x for x in d)
    return sum(d[i] * d[i + 1] for i in range(n - 1)) / den if den > 1e-12 else 0.0


def _flips(dirs: list[int]) -> float:
    moves = [d for d in dirs if d]
    return sum(1 for a, b in zip(moves, moves[1:]) if a != b) / (len(moves) - 1) if len(moves) > 1 else 0.0


def stats(samples: list[Sample], clean_s: float | None = None) -> dict | None:
    if not samples:
        return None
    n = len(samples)
    ra, dec = [s.ra for s in samples], [s.dec for s in samples]
    rms_ra, rms_dec = _rms(ra), _rms(dec)
    total = math.hypot(rms_ra, rms_dec)

    def axis(vals: list[float], rms: float, ms: list[float], dirs: list[int], limited: list[bool]) -> dict:
        return {
            "rms": round(rms, 3),
            "peak": round(max(abs(x) for x in vals), 3),
            "mean": round(sum(vals) / n, 3),
            "lag1": round(_lag1(vals), 2),
            "idle": round(sum(1 for m in ms if m <= 0) / n, 2),
            "flips": round(_flips(dirs), 2),
            "pulseMs": round(sum(ms) / n),
            "limited": round(sum(limited) / n, 2),
            "pulsesKnown": sum(ms) > 0,
        }

    hfd = _median([s.hfd for s in samples])
    return {
        "n": n,
        "seconds": round(clean_s if clean_s is not None else samples[-1].t - samples[0].t),
        "total": round(total, 3),
        "se": round(total / math.sqrt(2 * n), 4),
        "ra": axis(ra, rms_ra, [s.ra_ms for s in samples], [s.ra_dir for s in samples], [s.ra_limited for s in samples]),
        "dec": axis(dec, rms_dec, [s.dec_ms for s in samples], [s.dec_dir for s in samples], [s.dec_limited for s in samples]),
        "snr": round(_median([s.snr for s in samples]), 1),
        "mass": round(_median([s.mass for s in samples])),
        "hfdPx": round(hfd, 2),
        "oscillation": round(max(abs(x) for x in ra) / rms_ra, 2) if rms_ra > 1e-9 else 0.0,
    }


# ---------------------------------------------------------------------- diagnosis

OVERCORRECT_LAG1, WANDER_LAG1, WHITE_LAG1 = -0.30, 0.50, 0.25
OFFSET_RATIO, DEADBAND_IDLE, BUSY_IDLE, CHASING_FLIPS = 0.50, 0.50, 0.10, 0.45
LOW_SNR, MIN_SAMPLES = 8.0, 60

VERDICTS = {
    "healthy": ("Healthy", "Within target: leave it alone."),
    "overcorrecting": ("Overcorrecting", "Each correction overshoots. Lower aggressiveness (or raise RA hysteresis)."),
    "deadband_too_wide": ("Deadband too wide", "Slow wander that rarely gets a pulse. Lower min-move."),
    "undercorrecting": ("Undercorrecting", "Slow wander or a steady offset. Raise aggressiveness."),
    "chasing_seeing": ("Chasing seeing", "Pulses every frame, reversing about half the time. Raise min-move."),
    "seeing_limited": ("Seeing-limited", "The error looks like seeing; no parameter change is likely to help."),
}


def _axis_verdict(a: dict, axis_target: float) -> tuple[str, str]:
    offset = abs(a["mean"]) / a["rms"] if a["rms"] > 1e-9 else 0.0
    ev = f"lag1 {a['lag1']:+.2f}, offset {a['mean']:+.2f}″"
    if a["pulsesKnown"]:
        ev += f", idle {a['idle']:.0%}, reversals {a['flips']:.0%}"
    if a["rms"] <= axis_target:
        return "healthy", f"RMS {a['rms']:.2f}″ ≤ {axis_target:.2f}″"
    if a["lag1"] <= OVERCORRECT_LAG1:
        return "overcorrecting", ev
    wander = a["lag1"] >= WANDER_LAG1 or offset >= OFFSET_RATIO
    if wander and a["pulsesKnown"] and a["idle"] >= DEADBAND_IDLE:
        return "deadband_too_wide", ev
    if wander:
        return "undercorrecting", ev
    if a["pulsesKnown"] and abs(a["lag1"]) < WHITE_LAG1 and a["idle"] <= BUSY_IDLE and a["flips"] >= CHASING_FLIPS:
        return "chasing_seeing", ev
    return "seeing_limited", ev


def diagnose(st: dict | None, target_rms: float) -> dict:
    """Per-axis verdicts. `target_rms` is the total target in arcsec (split evenly over the axes)."""
    if not st:
        return {"axes": [], "caveats": ["No clean guiding samples yet."], "actionable": False}
    axis_target = target_rms / math.sqrt(2)
    axes = []
    for axis in ("ra", "dec"):
        verdict, evidence = _axis_verdict(st[axis], axis_target)
        title, hint = VERDICTS[verdict]
        axes.append({"axis": axis, "verdict": verdict, "title": title, "hint": hint, "evidence": evidence})
    caveats = []
    if st["n"] < MIN_SAMPLES:
        caveats.append(f"Only {st['n']} clean frames; the shape figures are noisy.")
    if st["snr"] and st["snr"] < LOW_SNR:
        caveats.append(f"Guide star SNR {st['snr']:.1f} is low: centroid noise, not the mount, may dominate. Try a brighter star or a longer guide exposure.")
    actionable = not caveats and any(a["verdict"] not in ("healthy", "seeing_limited") for a in axes)
    return {"axes": axes, "caveats": caveats, "actionable": actionable, "axisTarget": round(axis_target, 2)}
