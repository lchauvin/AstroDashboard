"""Sequence guard: stop the NINA sequence while guiding is lost (clouds, a lost star), start it again once
guiding has been steady for a while. Off by default: the toggle is on Live › Overview, the timings in
Settings → Live · Ingest.

Only a *lost star* counts. While PHD2 searches for its star it sends StarLost on every frame, and
GuideStep again once it has it back. A deliberate stop — a meridian flip, autofocus, centering, you —
sends GuidingStopped instead, which resets the guard rather than tripping it.

* stop: the sequence is running and the star has been lost for at least `stopLostPct` of the last `stopAfterS`
  (75 % of a minute by default). A share rather than an unbroken loss: under broken cloud PHD2 picks the star
  up for a few frames between two losses, and the subs are ruined all the same. Lost time runs from a StarLost
  to the next GuideStep; time before guiding started counts as not lost.
* resume: the guard stopped the sequence, it is still stopped, and PHD2 has guided for `resumeAfterS`
  without losing the star. The Advanced API starts it with `skipValidation`, which skips NINA's
  pre-sequence checklist ("… Continue anyway?"): asked, that dialog would wait for a click on the
  observatory PC (and hold the API call open meanwhile). NINA picks up at the first unfinished
  instruction; the exposure that was interrupted is taken again.
* give up: after `maxWaitMin` without steady guiding the guard forgets the pause; the sequence stays stopped.

Starting the sequence yourself in the meantime, or turning the guard off, cancels the pending resume.
PHD2 keeps searching around its lock position while the sequence is stopped; if the star has drifted
out of the search region by the time the sky clears, guiding does not come back by itself, and neither
does the sequence.
"""

from __future__ import annotations

import time
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "stopAfterS": 60, "stopLostPct": 75, "resumeAfterS": 60, "maxWaitMin": 120}
LIMITS = {"stopAfterS": (15, 1800), "stopLostPct": (50, 100), "resumeAfterS": (15, 1800), "maxWaitMin": (5, 720)}

STOP_GRACE_S = 30.0  # after a stop, NINA's polled state can still say "running" for a few seconds
FORGET_AFTER_S = 12 * 3600  # a pause saved before an engine restart is not resumed the next evening


class SequenceGuard:
    """The decisions only; the runtime feeds it PHD2 events and NINA's state and does the calls."""

    def __init__(self, store: Any) -> None:
        self.store = store
        saved = store.get_json("sequence_guard") or {}
        self.paused_at: float | None = saved.get("pausedAt")
        if self.paused_at and time.time() - self.paused_at > FORGET_AFTER_S:
            self.paused_at = None
        self.log: list[dict] = saved.get("log", [])[-50:]
        self.marks: list[tuple[float, bool]] = []  # (t, lost) at each switch between losing and guiding, oldest first
        self.steady_since: float | None = None  # first guide step after the last star loss
        self.error: str | None = None

    def save(self) -> None:
        self.store.set_json("sequence_guard", {"pausedAt": self.paused_at, "log": self.log[-50:]})

    def note(self, message: str, now: float | None = None) -> None:
        self.log.append({"t": now or time.time(), "message": message})
        self.save()

    def on_phd2_event(self, ev: str | None, now: float) -> None:
        if ev == "GuideStep":
            self._mark(now, False)
            self.steady_since = self.steady_since or now
        elif ev == "StarLost":
            self._mark(now, True)
            self.steady_since = None
        elif ev in ("GuidingStopped", "Paused", "StartCalibration"):
            self.reset_signals()

    def reset_signals(self) -> None:
        """Guiding was stopped on purpose, or PHD2 went away: neither a loss nor a steady run."""
        self.marks = []
        self.steady_since = None

    def _mark(self, now: float, lost: bool) -> None:
        if not self.marks or self.marks[-1][1] != lost:
            self.marks.append((now, lost))
        while len(self.marks) > 1 and self.marks[1][0] <= now - LIMITS["stopAfterS"][1]:
            self.marks.pop(0)  # the longest window no longer reaches it

    def lost_seconds(self, now: float, window: float) -> float:
        """How long the star has been lost within the last `window` seconds."""
        start, lost = now - window, 0.0
        for i, (t, is_lost) in enumerate(self.marks):
            end = self.marks[i + 1][0] if i + 1 < len(self.marks) else now
            if is_lost:
                lost += max(0.0, end - max(t, start))
        return lost

    def decide(self, now: float, s: dict, running: bool | None, guiding: bool) -> str | None:
        """"stop", "start" or None. `running`: NINA's sequence (None when unknown); `guiding`: PHD2 is guiding."""
        if self.paused_at is None:
            window = s["stopAfterS"]
            if s["enabled"] and running is True and self.lost_seconds(now, window) >= window * s["stopLostPct"] / 100:
                return "stop"
            return None
        if not s["enabled"]:
            self.forget("Guard turned off: the sequence stays stopped.", now)
        elif running is True and now - self.paused_at > STOP_GRACE_S:
            self.forget("The sequence was started outside the guard; nothing to resume.", now)
        elif now - self.paused_at > s["maxWaitMin"] * 60:
            self.forget(f"Guiding did not come back within {s['maxWaitMin']} min: gave up, the sequence stays stopped.", now)
        elif running is False and guiding and self.steady_since and now - self.steady_since >= s["resumeAfterS"]:
            return "start"
        return None

    def stopped(self, now: float, lost_s: float, window_s: float) -> None:
        self.paused_at = now
        self.error = None
        self.note(f"Guide star lost {lost_s:.0f} s of the last {window_s:.0f} s: stopped the sequence.", now)

    def started(self, now: float, steady_for: float) -> None:
        self.paused_at = None
        self.error = None
        self.note(f"Guiding steady for {steady_for:.0f} s: resumed the sequence.", now)

    def forget(self, message: str, now: float | None = None) -> None:
        self.paused_at = None
        self.note(message, now)

    def as_dict(self, now: float, s: dict) -> dict:
        lost = self.lost_seconds(now, s["stopAfterS"])
        if not s["enabled"]:
            state = "off"
        elif self.paused_at is not None:
            state = "paused"
        elif lost > 0:
            state = "lost"
        else:
            state = "watching"
        return {
            **{k: s[k] for k in DEFAULTS}, "state": state, "error": self.error,
            "pausedAt": self.paused_at,
            "lostS": round(lost), "lostPct": round(100 * lost / s["stopAfterS"]),
            "steadyForS": round(now - self.steady_since) if self.steady_since else None,
            "giveUpAt": self.paused_at + s["maxWaitMin"] * 60 if self.paused_at else None,
            "log": list(reversed(self.log[-10:])),
        }
