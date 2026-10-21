"""Sky monitor: tells NINA whether the sky is clear, as an ASCOM Alpaca safety monitor (`alpaca.py`).

The dashboard never drives NINA. It only answers NINA's safety-monitor poll, and the sequence decides what to
do about it with NINA's own instructions, for instance:

    Imaging — Loop Until Time / altitude (triggers: Restore Guiding, Center After Drift)
      ├─ Clear sky — Loop While Safe              NINA interrupts the exposure when unsafe (and does not save it)
      │    └─ the exposures, the dither
      └─ Wait for clear sky — Loop While Unsafe   PHD2 keeps guiding, searching for its star, meanwhile
           └─ Wait for Time Span 1 min            short waits: NINA checks triggers (the meridian flip) between instructions
    End of sequence — runs as usual

The evidence is PHD2's, one guiding run at a time (a run ends at GuidingStopped, Paused, a calibration or a newly
selected star; PHD2 going away ends it too):

* unsafe: the star has been lost (StarLost until the next GuideStep) for at least `lostPct` of the last `windowS`
  (75 % of a minute; a share, so broken cloud that PHD2 keeps losing and finding counts too), or its brightness
  (the median StarMass over the window) is below `dimPct` of its clear-sky brightness (50 %). Thin cloud and haze
  dim the star well before PHD2 loses it. The clear-sky brightness is the 80th percentile of the star's mass over
  the last 30 min of this run while the monitor said clear, once 2 min of it exist; it is frozen while unsafe,
  and a new guide exposure starts it again.
* clear again: PHD2 has guided `clearAfterS` (2 min) without losing the star, at `dimPct + CLEAR_MARGIN` of its
  clear-sky brightness or more over that time when that brightness is known: hysteresis against flapping.
* no evidence means clear: guiding stopped or paused (a flip, an autofocus, a slew, you), PHD2 gone, the monitor
  off. NINA is never held on old evidence; the next guiding run brings fresh evidence.
* obstructed: the mount points below the horizon profile of the site (Settings › Sites). A building or a tree is
  not cloud: the monitor says clear and leaves it to the sequence's own horizon or time conditions.
* give up: unsafe for `giveUpMin` (2 h) without a break is more likely an obstruction the profile lacks; the
  monitor says clear until the next guiding run, so a sequence without a time limit cannot wait forever.
"""

from __future__ import annotations

import time
from collections import deque
from statistics import median
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "windowS": 60, "lostPct": 75, "dimPct": 50, "clearAfterS": 120, "giveUpMin": 120}
LIMITS = {"windowS": (15, 1800), "lostPct": (50, 100), "dimPct": (10, 90), "clearAfterS": (15, 1800), "giveUpMin": (5, 720)}
FLAGS = ("enabled",)

CLEAR_MARGIN = 15  # % points above dimPct the star must be back at before the sky counts as clear
BASELINE_S = 1800.0  # the clear-sky brightness comes from this much of the run's clear guiding…
BASELINE_MIN_S = 120.0  # …and needs at least this much of it
BASELINE_MIN_STEPS = 10
BASELINE_PCTL = 0.80
MIN_STEPS = 5  # guide steps in a window for its brightness to mean anything
KEEP_S = max(BASELINE_S, LIMITS["windowS"][1], LIMITS["clearAfterS"][1])

# PHD2 events that end a guiding run, and how the log says it
ENDS_RUN = {"GuidingStopped": "Guiding stopped", "Paused": "Guiding paused", "StartCalibration": "PHD2 started calibrating",
            "StarSelected": "PHD2 selected a guide star"}


def horizon_at(horizon: list[dict], az: float) -> float | None:
    """The horizon profile's altitude at `az`, linear between its points (as Planning's `horizonAt`, without the
    site's minimum altitude: that is a preference, not an obstruction). None without a profile."""
    prof = sorted((float(p["az"]) % 360, float(p["alt"])) for p in horizon or [] if isinstance(p, dict) and "az" in p and "alt" in p)
    if not prof:
        return None
    if len(prof) == 1:
        return prof[0][1]
    az %= 360
    hi = next((i for i, (a, _) in enumerate(prof) if a >= az), 0)
    (lo_az, lo_alt), (hi_az, hi_alt) = prof[hi - 1], prof[hi]  # hi = 0 wraps round to the last point
    span = (hi_az - lo_az) % 360 or 360
    return lo_alt + (hi_alt - lo_alt) * ((az - lo_az) % 360) / span


class SkyMonitor:
    """The verdict only; the runtime feeds it PHD2's events and the mount's pointing, `alpaca.py` serves it."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.log: list[dict] = (store.get_json("sky_monitor") or {}).get("log", [])[-50:]
        self.cloudy_since: float | None = None
        self.reason: str | None = None  # why it is unsafe
        self.gave_up = False  # unsafe too long: clear until the next guiding run
        self.obstructed: str | None = None  # what the mount points at, when below the horizon profile
        self.polled: dict | None = None  # NINA's last IsSafe question: {"t", "client"} (set by the Alpaca server)
        self.server: dict = {"running": False, "port": None, "error": None, "discovery": False}
        self._reset_run()

    def _reset_run(self) -> None:
        self.marks: list[tuple[float, bool]] = []  # (t, lost) at each switch between losing and guiding, oldest first
        self.steady_since: float | None = None  # first guide step after the last star loss
        self.steps: deque[tuple[float, float, bool]] = deque()  # (t, star mass, said clear) of this run's guide steps
        self.exposure_ms: int | None = None
        self._base: float | None = None  # the last clear-sky brightness worked out

    @property
    def safe(self) -> bool:
        return self.cloudy_since is None

    def save(self) -> None:
        self.store.set_json("sky_monitor", {"log": self.log[-50:]})

    def note(self, message: str, now: float | None = None) -> None:
        self.log.append({"t": now or time.time(), "message": message})
        self.save()

    # ------------------------------------------------------------------ evidence

    def on_phd2_event(self, msg: dict, now: float, exposure_ms: int | None = None) -> None:
        ev = msg.get("Event")
        if ev == "GuideStep":
            if exposure_ms and self.exposure_ms and exposure_ms != self.exposure_ms:
                self.steps.clear()  # the star mass scales with the exposure: its clear-sky brightness starts again
                self._base = None
            self.exposure_ms = exposure_ms or self.exposure_ms
            self._mark(now, False)
            self.steady_since = self.steady_since or now
            mass = msg.get("StarMass")
            if isinstance(mass, (int, float)) and not isinstance(mass, bool) and mass > 0:
                self.steps.append((now, float(mass), self.safe))
            while self.steps and self.steps[0][0] < now - KEEP_S:
                self.steps.popleft()
        elif ev == "StarLost":
            self._mark(now, True)
            self.steady_since = None
        elif ev in ENDS_RUN:
            self.end_run(ENDS_RUN[ev], now)

    def end_run(self, why: str, now: float | None = None) -> None:
        """The guiding run is over: no more evidence, so clear, and the next run starts from nothing."""
        if not self.safe:
            self._clear(f"{why}, so there is no more evidence; the next guiding run decides", now)
        self.gave_up = False
        self._reset_run()

    def _mark(self, now: float, lost: bool) -> None:
        if not self.marks or self.marks[-1][1] != lost:
            self.marks.append((now, lost))
        while len(self.marks) > 1 and self.marks[1][0] <= now - KEEP_S:
            self.marks.pop(0)  # the longest window no longer reaches it

    def lost_seconds(self, now: float, window: float) -> float:
        """How long the star has been lost within the last `window` seconds."""
        start, lost = now - window, 0.0
        for i, (t, is_lost) in enumerate(self.marks):
            end = self.marks[i + 1][0] if i + 1 < len(self.marks) else now
            if is_lost:
                lost += max(0.0, end - max(t, start))
        return lost

    def baseline(self, now: float) -> float | None:
        """The star's clear-sky brightness (mass): worked out while clear, kept as it was while unsafe."""
        if self.safe:
            clear = [(t, m) for t, m, ok in self.steps if ok and t >= now - BASELINE_S]
            if len(clear) >= BASELINE_MIN_STEPS and clear[-1][0] - clear[0][0] >= BASELINE_MIN_S:
                masses = sorted(m for _, m in clear)
                self._base = masses[int(BASELINE_PCTL * (len(masses) - 1))]
        return self._base

    def brightness(self, now: float, span: float) -> float | None:
        """The star's median mass over the last `span` seconds, as a share of its clear-sky brightness."""
        base = self.baseline(now)
        recent = [m for t, m, _ in self.steps if t >= now - span]
        if not base or len(recent) < MIN_STEPS:
            return None
        return median(recent) / base

    # ------------------------------------------------------------------ verdict

    def tick(self, now: float, s: dict, obstructed: str | None = None) -> None:
        """Re-decide. `obstructed`: what the mount points at when it is below the horizon profile, else None."""
        self.obstructed = obstructed if s["enabled"] else None
        if not s["enabled"]:
            if not self.safe:
                self._clear("the sky monitor was turned off", now)
            return
        if obstructed:
            if not self.safe:
                self._clear(f"{obstructed}, which is not cloud", now)
            return
        if self.safe:
            if self.gave_up:
                return
            w = s["windowS"]
            lost = self.lost_seconds(now, w)
            if lost >= w * s["lostPct"] / 100:
                self._cloudy(f"guide star lost {lost:.0f} s of the last {w:.0f} s", now)
            elif (b := self.brightness(now, w)) is not None and b < s["dimPct"] / 100:
                self._cloudy(f"guide star down to {b:.0%} of its clear-sky brightness", now)
            return
        assert self.cloudy_since is not None
        if now - self.cloudy_since >= s["giveUpMin"] * 60:
            self.gave_up = True
            self._clear(f"unsafe for {s['giveUpMin']} min without a break, more likely an obstruction than cloud; "
                        "clear until the next guiding run", now)
            return
        c = s["clearAfterS"]
        if self.steady_since is None or now - self.steady_since < c:
            return
        b = self.brightness(now, c)
        if b is not None and b < self.clear_need(s) / 100:
            return
        self._clear(f"guide star steady for {now - self.steady_since:.0f} s" + (f" at {b:.0%} of its clear-sky brightness" if b else ""), now)

    def clear_now(self, now: float | None = None) -> None:
        """You say the sky is fine (or it is an obstruction): clear until the next guiding run."""
        self.gave_up = True
        self._clear("you said so; clear until the next guiding run", now)

    @staticmethod
    def clear_need(s: dict) -> float:
        return min(95, s["dimPct"] + CLEAR_MARGIN)

    def _cloudy(self, why: str, now: float) -> None:
        self.baseline(now)  # freeze the clear-sky brightness as it was just before
        self.cloudy_since, self.reason = now, why
        self.note(f"Unsafe: {why}.", now)

    def _clear(self, why: str, now: float | None) -> None:
        self.cloudy_since, self.reason = None, None
        self.note(f"Clear: {why}.", now)

    def as_dict(self, now: float, s: dict) -> dict:
        w = s["windowS"]
        lost = self.lost_seconds(now, w)
        b = self.brightness(now, w)
        if not s["enabled"]:
            state = "off"
        elif not self.safe:
            state = "cloudy"
        elif self.obstructed:
            state = "obstructed"
        elif self.gave_up:
            state = "gaveUp"
        elif self.marks:
            state = "watching"
        else:
            state = "idle"
        return {
            **{k: s[k] for k in DEFAULTS}, "state": state, "safe": self.safe, "reason": self.reason,
            "cloudySince": self.cloudy_since, "obstructed": self.obstructed,
            "lostS": round(lost), "lostSharePct": round(100 * lost / w),
            "brightnessPct": round(100 * b) if b is not None else None, "baselineKnown": self.baseline(now) is not None,
            "clearNeedPct": self.clear_need(s),
            "steadyForS": round(now - self.steady_since) if self.steady_since else None,
            "giveUpAt": self.cloudy_since + s["giveUpMin"] * 60 if self.cloudy_since else None,
            "polled": self.polled, "server": self.server,
            "log": list(reversed(self.log[-10:])),
        }
