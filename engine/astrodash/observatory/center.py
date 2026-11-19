"""Center on it: put a session's target back in the middle of the frame, on a click (never automatically).

The coordinates are the session's NINA target (OBJCTRA/OBJCTDEC/OBJCTROT of its lights: the centre the
sequence's Slew and center aims at, before any dither).

Two actions (`mode`):

* **center** ("Center now"). Sequence running (on that target): wait for the exposure in progress to be saved,
  stop the sequence, rewrite the target container's coordinates, slew and centre (NINA's own Center instruction,
  at its pointing tolerance), check that PHD2 guides again and has settled, then start the sequence (NINA resumes
  at the first unfinished instruction). When anything fails after the stop, the sequence stays stopped and a
  notification says why. Sequence not running: slew and centre now; the sequence is not touched.
* **update** ("Update sequence"): only rewrite the target container's coordinates, running or not, so the
  sequence's own Slew and center (or its centre after a flip) goes there. In NINA's memory only: the sequence file
  is not rewritten.

NINA's API has no pause: a stop is what there is, hence waiting for the frame first. The target's name and
rotation are kept (`/sequence/set-target` rewrites them too), so the frames' OBJECT does not change. Which
container `index` designates is NINA's business; it is checked after the write, and a wrong container is put back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx

from . import phone
from .equipment import _seconds_until
from .nina import NinaError, NinaUnavailable, parse_sequence, target_containers

if TYPE_CHECKING:
    from .runtime import Observatory

log = logging.getLogger(__name__)

FRAME_GRACE_S = 120.0      # beyond the exposure's own end, for download and saving
STOP_WAIT_S = 60.0
GUIDE_RESTART_S = 45.0     # NINA's Center restarts guiding itself when it stopped it; after that we ask NINA
SETTLED_S = 15.0           # guiding, no settling, for this long
GUIDE_TIMEOUT_S = 240.0
COORD_EPS_DEG = 2 / 3600   # set-target written = read back within 2″
POLL_S = 1.0
BUSY_WAIT_S = 1200.0       # a meridian flip or an autofocus that NINA is doing: wait for it, up to this
BUSY = ("meridian flip", "autofocus")

ACTIVE = ("waiting-frame", "stopping", "updating", "centering", "guiding", "resuming")


class CenterRefused(RuntimeError):
    """Not started: nothing was changed in NINA."""


def _same(a: float | None, b: float | None, eps: float = COORD_EPS_DEG) -> bool:
    if a is None or b is None:
        return False
    d = abs(a - b) % 360
    return min(d, 360 - d) <= eps


def _fmt_ra(deg: float) -> str:
    tenths = round((deg / 15) % 24 * 36000) % (24 * 36000)
    return f"{tenths // 36000:02d}h{tenths % 36000 // 600:02d}m{tenths % 600 / 10:04.1f}s"


def _fmt_dec(deg: float) -> str:
    secs = round(abs(deg) * 3600)
    return f"{'-' if deg < 0 else '+'}{secs // 3600:02d}°{secs % 3600 // 60:02d}′{secs % 60:02d}″"


def _names(t: dict) -> set[str]:
    """The names this target goes by in NINA (its OBJECT on this night, and on the revision's other nights)."""
    return {str(n).strip().casefold() for n in [t.get("object"), *(t.get("names") or [])] if n}


def describe(t: dict) -> str:
    return f"{t.get('object') or 'target'} (RA {_fmt_ra(t['ra'])}, Dec {_fmt_dec(t['dec'])})"


class CenterJob:
    def __init__(self, obs: "Observatory", sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.obs = obs
        self._sleep = sleep
        self.task: asyncio.Task | None = None
        self.state: dict[str, Any] | None = None
        self._cancel = False

    # ------------------------------------------------------------------ public

    def as_dict(self) -> dict | None:
        return dict(self.state) if self.state else None

    @property
    def busy(self) -> bool:
        return bool(self.state and self.state["phase"] in ACTIVE)

    def start(self, target: dict, wait_frame: bool = True, mode: str = "center") -> dict:
        """`target`: {"object", "ra", "dec", "rotation", "revision", "session", "panel"} (J2000 degrees).
        `mode`: "center" (slew and centre now) or "update" (rewrite the sequence's target only)."""
        if mode not in ("center", "update"):
            raise CenterRefused(f"Unknown action {mode!r}.")
        if self.busy:
            raise CenterRefused("A Center on it is already under way.")
        if not self.obs.nina or not self.obs.nina_status.get("connected"):
            raise CenterRefused("NINA is not reachable.")
        running = self.obs.sequence.get("status") == "running"
        if (mode == "update" or running) and not self.obs.sequence.get("available"):
            raise CenterRefused("No advanced sequence is loaded in NINA.")
        if mode == "center":
            mount = self.obs.equipment.get("mount") or {}
            if not mount.get("Connected"):
                raise CenterRefused("The mount is not connected in NINA.")
            if mount.get("AtPark"):
                raise CenterRefused("The mount is parked.")
        if mode == "center" and running:
            seq_target = self.obs.sequence.get("target")
            if seq_target and _names(target) and seq_target.strip().casefold() not in _names(target):
                raise CenterRefused(f'NINA is imaging "{seq_target}", not "{target["object"]}": nothing changed.')
            if busy := self._ninas_busy():
                raise CenterRefused(f"NINA is in the middle of a {busy}: try again once it is done.")
        self._cancel = False
        stops = mode == "center" and running
        self.state = {"phase": "waiting-frame" if stops else ("centering" if mode == "center" else "updating"),
                      "mode": mode, "running": stops, "target": target,
                      "message": "Starting…", "started": time.time(), "finished": None, "error": None,
                      "sequenceStopped": False, "waitFrame": wait_frame}
        run = self._run(target, True, wait_frame) if stops else (self._center_only(target) if mode == "center" else self._update_only(target))
        self.task = asyncio.create_task(run, name="obs-center")
        return self.as_dict()

    def cancel(self) -> dict | None:
        """Only before the sequence is stopped (while waiting for the frame): afterwards, finishing is safer."""
        if self.state and self.state["phase"] == "waiting-frame":
            self._cancel = True
        elif self.busy:
            raise CenterRefused("Too late to cancel: the sequence has been stopped. Wait for it to finish.")
        return self.as_dict()

    # ------------------------------------------------------------------ steps

    def _phase(self, phase: str, message: str) -> None:
        assert self.state is not None
        self.state.update(phase=phase, message=message)
        log.info("center: %s — %s", phase, message)

    def _finish(self, phase: str, message: str, error: str | None = None) -> None:
        assert self.state is not None
        self.state.update(phase=phase, message=message, error=error, finished=time.time())
        (log.warning if error else log.info)("center: %s — %s", phase, message)

    async def _update_only(self, target: dict) -> None:
        try:
            self._phase("updating", "Updating the target in NINA's sequence…")
            note = await self._set_target(target)
            self._finish("done", f"NINA's sequence now targets {describe(target)}. {note}".strip())
        except (CenterRefused, NinaError, NinaUnavailable) as exc:
            self._finish("failed", str(exc), error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("center (update) failed")
            self._finish("failed", f"{type(exc).__name__}: {exc}", error=str(exc))

    async def _center_only(self, target: dict) -> None:
        """No sequence running: slew and centre now, the sequence untouched."""
        nina = self.obs.nina
        assert nina is not None
        try:
            self._phase("centering", f"Slewing and centering on {describe(target)}…")
            await nina.slew_center(target["ra"], target["dec"])
            self._finish("done", f"Centered on {describe(target)}. The sequence was not changed.")
        except (CenterRefused, NinaError, NinaUnavailable) as exc:
            self._finish("failed", f"NINA: {exc}", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("center (slew) failed")
            self._finish("failed", f"{type(exc).__name__}: {exc}", error=str(exc))

    async def _run(self, target: dict, running: bool, wait_frame: bool) -> None:
        nina = self.obs.nina
        assert nina is not None
        try:
            if wait_frame:
                await self._wait_frame()
            if self._cancel:
                self._finish("cancelled", "Cancelled: the sequence was not touched.")
                return
            await self._wait_not_busy()
            if self._cancel:
                self._finish("cancelled", "Cancelled: the sequence was not touched.")
                return
            seq = await self._sequence()
            if seq.get("status") != "running":  # it ended (or was stopped) meanwhile: just update the target
                self._phase("updating", "The sequence is no longer running: updating its target only…")
                assert self.state is not None
                self.state["running"] = False
                note = await self._set_target(target)
                self._finish("done", f"NINA's sequence now targets {describe(target)}. {note}".strip())
                return
            now_on = self.obs.sequence.get("target") or seq.get("target")
            if now_on and _names(target) and now_on.strip().casefold() not in _names(target):
                raise CenterRefused(f'NINA moved on to "{now_on}" meanwhile: nothing changed.')
            guiding_before = self._guiding()
            self._phase("stopping", "Stopping the sequence…")
            await nina.sequence_stop()
            assert self.state is not None
            self.state["sequenceStopped"] = True
            await self._wait_stopped()
        except CenterRefused as exc:
            self._finish("failed", str(exc), error=str(exc))
            return
        except (NinaError, NinaUnavailable) as exc:
            self._finish("failed", f"NINA: {exc}", error=str(exc))
            if self.state and self.state["sequenceStopped"]:
                await self._notify_stopped(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("center failed")
            self._finish("failed", f"{type(exc).__name__}: {exc}", error=str(exc))
            return

        # From here the sequence is stopped: any failure leaves it stopped, and says so.
        try:
            self._phase("updating", "Updating the target in NINA's sequence…")
            try:
                note = await self._set_target(target)
            except (CenterRefused, NinaError, NinaUnavailable) as exc:
                note = f"The sequence's target was not updated ({exc})."
            self._phase("centering", f"Slewing and centering on {describe(target)}…")
            await nina.slew_center(target["ra"], target["dec"])
            if guiding_before:
                self._phase("guiding", "Waiting for guiding to restart and settle…")
                await self._wait_guiding()
            self._phase("resuming", "Starting the sequence again…")
            await nina.sequence_start()
            assert self.state is not None
            self.state["sequenceStopped"] = False
            self._finish("done", f"Centered on {describe(target)}; the sequence is running again. {note}".strip())
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (NinaError, NinaUnavailable, CenterRefused, TimeoutError)):
                log.exception("center failed")
            reason = str(exc) or type(exc).__name__
            self._finish("failed", f"{reason} The sequence is left stopped: start it again in NINA.", error=reason)
            await self._notify_stopped(reason)

    async def _wait_frame(self) -> None:
        cam = self.obs.equipment.get("camera") or {}
        if not cam.get("IsExposing"):
            return
        left = _seconds_until(cam.get("ExposureEndTime")) or 0.0
        saves = self.obs.image_saves
        deadline = time.monotonic() + max(0.0, left) + FRAME_GRACE_S
        self._phase("waiting-frame", f"Waiting for the exposure in progress to be saved ({max(0, round(left))} s left)…")
        while self.obs.image_saves == saves:
            if self._cancel:
                return
            if time.monotonic() > deadline:
                raise CenterRefused("The exposure in progress was never saved (no IMAGE-SAVE from NINA): nothing changed.")
            await self._sleep(POLL_S)

    def _ninas_busy(self) -> str | None:
        """A flip or autofocus NINA has announced and not finished (not older than 15 min: an end event may be missed)."""
        now = time.time()
        return next((r for r, iv in self.obs.dist.open.items() if r in BUSY and now - iv.start < 900), None)

    async def _wait_not_busy(self) -> None:
        """A trigger (autofocus, meridian flip) may run right after the frame: let NINA finish it first."""
        deadline = time.monotonic() + BUSY_WAIT_S
        while (busy := self._ninas_busy()) and not self._cancel:
            self._phase("waiting-frame", f"Waiting for NINA's {busy} to finish…")
            if time.monotonic() > deadline:
                raise CenterRefused(f"NINA's {busy} did not finish within {BUSY_WAIT_S / 60:.0f} min: nothing changed.")
            await self._sleep(POLL_S * 5)

    async def _sequence(self, deadline_s: float = STOP_WAIT_S) -> dict:
        """The sequence's state from `/sequence/json` (small, unlike `/sequence/state`), retried while NINA is slow."""
        nina = self.obs.nina
        assert nina is not None
        deadline = time.monotonic() + deadline_s
        while True:
            try:
                return parse_sequence(await nina.get("/sequence/json", timeout=30.0))
            except NinaUnavailable:
                if time.monotonic() > deadline:
                    raise
            await self._sleep(POLL_S * 2)

    async def _wait_stopped(self) -> None:
        deadline = time.monotonic() + STOP_WAIT_S
        while True:
            if (await self._sequence()).get("status") != "running":
                return
            if time.monotonic() > deadline:
                raise NinaError("the sequence still runs after /sequence/stop")
            await self._sleep(POLL_S)

    async def _set_target(self, target: dict) -> str:
        """Rewrite the coordinates of the target container named like the session's target (name and rotation kept)."""
        nina = self.obs.nina
        assert nina is not None
        before = target_containers(await nina.sequence_state())
        names = _names(target)
        idx = [i for i, c in enumerate(before) if c["name"].casefold() in names]
        if len(idx) > 1:  # the one NINA is running, if that settles it
            idx = [i for i in idx if before[i]["status"] == "RUNNING"] or idx
        if not idx:
            raise CenterRefused(f'No target container named "{target.get("object")}" in NINA\'s sequence.')
        if len(idx) > 1:
            raise CenterRefused(f'{len(idx)} target containers are named "{target.get("object")}": not guessing which one.')
        i = idx[0]
        mine = before[i]
        if _same(mine["ra"], target["ra"]) and _same(mine["dec"], target["dec"]):
            return "The sequence's target already had these coordinates."
        await nina.set_target(i, mine["name"], target["ra"], target["dec"], mine["rotation"])
        after = target_containers(await nina.sequence_state())
        if len(after) == len(before) and _same(after[i]["ra"], target["ra"]) and _same(after[i]["dec"], target["dec"]) \
                and after[i]["name"] == mine["name"]:
            return "The sequence's target was updated (in NINA's memory: save the sequence in NINA to keep it)."
        # Index i did not designate that container. All that is known is that i writes whichever container
        # changed, so put it back through i (not through its own position, which NINA may number differently).
        changed = [b for b, a in zip(before, after)
                   if not _same(a["ra"], b["ra"]) or not _same(a["dec"], b["dec"]) or a["name"] != b["name"]]
        if len(changed) == 1 and changed[0]["ra"] is not None:
            b = changed[0]
            with contextlib.suppress(NinaError, NinaUnavailable):
                await nina.set_target(i, b["name"], b["ra"], b["dec"], b["rotation"])
            raise CenterRefused(f'NINA updated the "{b["name"]}" container instead; it was put back')
        raise CenterRefused("NINA's sequence did not take the new coordinates as expected; check its targets in NINA")

    def _guiding(self) -> bool:
        p = self.obs.phd2
        if p and p.connected:
            return p.state.app_state == "Guiding"
        return str((self.obs.equipment.get("guider") or {}).get("State") or "").casefold() == "guiding"

    async def _wait_guiding(self) -> None:
        nina = self.obs.nina
        assert nina is not None
        start = time.monotonic()
        asked = False
        steady_since: float | None = None
        while True:
            now = time.monotonic()
            settling = "settling" in self.obs.dist.active()
            if self._guiding() and not settling:
                steady_since = steady_since or now
                if now - steady_since >= SETTLED_S:
                    return
            else:
                steady_since = None
                if not self._guiding() and not asked and now - start > GUIDE_RESTART_S:
                    asked = True
                    self._phase("guiding", "Guiding did not restart by itself: asking NINA to start it…")
                    await nina.guider_start()
            if now - start > GUIDE_TIMEOUT_S:
                raise TimeoutError("Guiding did not restart and settle within 4 minutes.")
            await self._sleep(POLL_S)

    async def _notify_stopped(self, reason: str) -> None:
        """A phone notification through the Planning ntfy settings, when they are on: the sequence is stopped."""
        t = (self.state or {}).get("target") or {}
        body = f"Center on it failed for {t.get('object') or 'the target'}: {reason}. NINA's sequence is stopped."
        await phone.notify(self.obs._settings(), "Sequence stopped", body)
