"""The observatory runtime: owns the NINA and PHD2 connections and every background task.

Tasks (each restarts with backoff when its connection drops):

* ``phd2``      guide steps, state, RPC
* ``nina_ws``   websocket events; every (re)connect re-reads REST state
* ``nina_poll`` equipment + sequence every few seconds (NINA has no per-step event), image history
* ``advisor``   measures applied changes; closes polar-alignment windows; asks the model when enabled and worth it
* ``frames``    notices the newest file in .Download (cheap: a directory walk, no reads)

Everything runs on the engine's event loop. Reads from HTTP handlers only touch plain dicts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any, Callable

from . import advisor as adv
from . import equipment as eq
from . import frames as fr
from . import guard as gd
from . import guiding, llm, polar
from .nina import NinaError, NinaRest, NinaUnavailable, listen, parse_sequence, state_targets
from .phd2 import Phd2Client, Phd2Disconnected, Phd2Error

log = logging.getLogger(__name__)

DEFAULTS = {
    "observatory": {"ninaHost": "127.0.0.1", "ninaPort": 1888, "phd2Host": "127.0.0.1", "phd2Instance": 1},
    "advisor": {"enabled": False, "model": "ollama/llama3.1:8b", "ollamaUrl": "http://localhost:11434",
                "intervalMin": 10, "targetRms": 0.8, "effort": "none", "autoApply": False},
    "guard": dict(gd.DEFAULTS),
}

POLL_S = 4.0
HISTORY_S = 30.0
ADVISOR_TICK_S = 20.0
GUARD_TICK_S = 5.0
LOST_KEEP_S = 1800.0
PHD2_ERRORS = (Phd2Error, Phd2Disconnected, asyncio.TimeoutError)


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("analysis failed", exc_info=task.exception())


class Observatory:
    def __init__(self, settings: Callable[[], dict], store: Any, download_root: Path,
                 nina_root: str | None = None, phd2_addr: tuple[str, int] | None = None) -> None:
        """`settings()` returns the dashboard settings; `nina_root`/`phd2_addr` override them (simulator)."""
        self._settings = settings
        self.download_root = download_root
        self._nina_override, self._phd2_override = nina_root, phd2_addr
        self.buffer = guiding.GuideBuffer()
        self.dist = guiding.Disturbances()
        self.advisor = adv.Advisor(store)
        self._analysis_task: asyncio.Task | None = None
        self.guard = gd.SequenceGuard(store)
        self.polar = polar.PolarTracker(store)
        self.phd2: Phd2Client | None = None
        self.nina: NinaRest | None = None
        self.tasks: list[asyncio.Task] = []
        self.nina_status: dict[str, Any] = {"connected": False, "error": None, "lastOk": None, "url": None}
        self.ws_connected = False
        self.phd2_error: str | None = None
        self.equipment: dict[str, Any] = {}
        self.sequence: dict[str, Any] = parse_sequence(None)
        self.history: list[dict] = []
        self.frames: list[dict] = []
        self.newest_file: dict | None = None
        self._stable_file: dict | None = None  # the newest file seen twice at the same size (complete)
        self._seen: dict[str, str] = {}  # device key → name, for everything connected at some point
        self._lost: dict[str, float] = {}
        self.advisor_check: dict | None = None  # last periodic decision (no model call), for the UI
        self._wake_frames = asyncio.Event()
        self._wake_history = asyncio.Event()

    # ------------------------------------------------------------------ config

    def adv_settings(self) -> dict:
        return {**DEFAULTS["advisor"], **(self._settings().get("advisor") or {})}

    def guard_settings(self) -> dict:
        return {**DEFAULTS["guard"], **(self._settings().get("guard") or {})}

    def obs_settings(self) -> dict:
        return {**DEFAULTS["observatory"], **(self._settings().get("observatory") or {})}

    def nina_root(self) -> str:
        if self._nina_override:
            return self._nina_override
        o = self.obs_settings()
        return f"http://{o['ninaHost']}:{int(o['ninaPort'])}"

    def phd2_addr(self) -> tuple[str, int]:
        if self._phd2_override:
            return self._phd2_override
        o = self.obs_settings()
        return o["phd2Host"], 4400 + int(o["phd2Instance"]) - 1

    @property
    def simulated(self) -> bool:
        return self._nina_override is not None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.nina = NinaRest(self.nina_root())
        host, port = self.phd2_addr()
        self.phd2 = Phd2Client(host, port, on_event=self._on_phd2_event)
        self.nina_status["url"] = self.nina_root()
        self.tasks = [
            asyncio.create_task(self._supervise("phd2", self._run_phd2), name="obs-phd2"),
            asyncio.create_task(self._supervise("nina_ws", self._run_ws), name="obs-nina-ws"),
            asyncio.create_task(self._poll_nina(), name="obs-nina-poll"),
            asyncio.create_task(self._advisor_loop(), name="obs-advisor"),
            asyncio.create_task(self._guard_loop(), name="obs-guard"),
            asyncio.create_task(self._frames_loop(), name="obs-frames"),
        ]
        log.info("observatory: NINA %s, PHD2 %s:%d%s", self.nina_root(), host, port, " (simulated)" if self.simulated else "")

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(BaseException):
                await t
        self.tasks = []
        if self.phd2:
            await self.phd2.close()
        if self.nina:
            await self.nina.aclose()

    async def reconfigure(self) -> None:
        """Connection settings changed: drop everything and reconnect."""
        await self.stop()
        self.nina_status = {"connected": False, "error": None, "lastOk": None, "url": None}
        self.equipment, self._seen, self._lost = {}, {}, {}
        self.sequence = parse_sequence(None)
        await self.start()

    async def _supervise(self, name: str, fn) -> None:
        delay = 2.0
        while True:
            started = time.monotonic()
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — offline is a normal state
                msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                if name == "phd2":
                    self.phd2_error = msg
                log.debug("%s ended: %s", name, msg)
            if time.monotonic() - started > 60:
                delay = 2.0
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)

    # ------------------------------------------------------------------ PHD2

    async def _run_phd2(self) -> None:
        assert self.phd2 is not None
        try:
            await self.phd2.run()
        finally:
            self.dist.begin("disconnected")
            self.guard.reset_signals()

    def _on_phd2_event(self, msg: dict) -> None:
        ev = msg.get("Event")
        d = self.dist
        self.guard.on_phd2_event(ev, time.time())
        if ev == "GuideStep":
            if self.phd2 and self.phd2.state.pixel_scale:
                self.buffer.scale = self.phd2.state.pixel_scale
            d.end("disconnected")
            d.end("stopped")
            self.buffer.add(msg)
        elif ev in ("SettleBegin", "Settling"):
            d.begin("settling")
        elif ev == "SettleDone":
            d.end("settling")
        elif ev == "GuidingDithered":
            d.mark("dither", label="dither")
        elif ev == "StartCalibration":
            d.begin("calibrating")
            d.events.append((time.time(), "calibration"))
        elif ev in ("CalibrationComplete", "CalibrationFailed"):
            d.end("calibrating")
        elif ev == "Paused":
            d.begin("paused")
        elif ev == "Resumed":
            d.end("paused")
        elif ev in ("StarLost", "LockPositionLost"):
            d.mark("star lost", label="star lost")
        elif ev == "GuidingStopped":
            d.begin("stopped")
        elif ev == "StartGuiding":
            d.end("stopped")

    # ------------------------------------------------------------------ NINA

    async def _run_ws(self) -> None:
        assert self.nina is not None
        try:
            await listen(self.nina.ws_url, self._on_nina_event, self._on_ws_connect)
        finally:
            self.ws_connected = False

    async def _on_ws_connect(self) -> None:
        self.ws_connected = True
        self._wake_history.set()

    async def _on_nina_event(self, event: str, payload: dict) -> None:
        d = self.dist
        if event == "MOUNT-BEFORE-FLIP":
            d.begin("meridian flip")
            d.events.append((time.time(), "meridian flip"))
        elif event == "MOUNT-AFTER-FLIP":
            d.end("meridian flip")
        elif event == "AUTOFOCUS-STARTING":
            d.begin("autofocus")
            d.events.append((time.time(), "autofocus"))
        elif event == "AUTOFOCUS-FINISHED":
            d.end("autofocus")
        elif event == "IMAGE-SAVE":
            self._wake_history.set()
            self._wake_frames.set()
        elif event in ("SEQUENCE-STARTING", "SEQUENCE-FINISHED"):
            with contextlib.suppress(NinaError, NinaUnavailable):
                await self._refresh_sequence()

    async def _refresh_sequence(self) -> None:
        assert self.nina is not None
        try:
            tree = await self.nina.get("/sequence/json")
        except NinaError as exc:  # an uninitialised sequencer is a normal state
            self.sequence = {**parse_sequence(None), "error": str(exc)}
            return
        try:
            # Only /sequence/state carries each target container's TargetName (older plugins lack it).
            targets = state_targets(await self.nina.get("/sequence/state"))
        except NinaError:
            targets = {}
        self.sequence = parse_sequence(tree, targets)

    async def _poll_nina(self) -> None:
        last_history = 0.0
        while True:
            try:
                assert self.nina is not None
                equipment = await self.nina.equipment()
                await self._refresh_sequence()
                self._track_devices(equipment)
                self.equipment = equipment
                self.nina_status.update(connected=True, error=None, lastOk=time.time())
                if self._wake_history.is_set() or time.monotonic() - last_history > HISTORY_S:
                    self._wake_history.clear()
                    self.history = await self.nina.image_history()
                    self.frames = fr.tonight(self.history)
                    last_history = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.nina_status.update(connected=False, error=f"{type(exc).__name__}: {exc}")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_history.wait(), POLL_S)

    def _track_devices(self, equipment: dict) -> None:
        now = time.time()
        connected = eq.connected_keys(equipment)
        for key in connected:
            self._seen[key] = str((equipment.get(key) or {}).get("Name") or eq.LABELS.get(key, key))
            self._lost.pop(key, None)
        for key in set(self._seen) - connected:
            self._lost.setdefault(key, now)
        for key, since in list(self._lost.items()):
            if now - since > LOST_KEEP_S:
                self._lost.pop(key)
                self._seen.pop(key, None)

    # ------------------------------------------------------------------ frames

    async def _frames_loop(self) -> None:
        while True:
            if not self.simulated:
                # No minimum age: the files are written by the observatory PC, whose clock may be ahead of
                # ours, so their mtime can be in "our" future. Completeness is judged in latest_frame instead.
                found = await asyncio.to_thread(fr.newest_file, self.download_root, min_age_s=0)
                prev = self.newest_file
                if found:
                    found["stable"] = bool(prev and prev["path"] == found["path"] and prev["size"] == found["size"])
                    if found["stable"]:
                        self._stable_file = found
                self.newest_file = found
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_frames.wait(), 10.0)
            self._wake_frames.clear()
            await asyncio.sleep(1.5)  # let the file finish landing after IMAGE-SAVE

    def latest_frame(self) -> dict | None:
        """Which image is "the last one".

        The newest file in .Download when NINA has announced it (its name is NINA's newest frame, so it is
        complete), or when NINA is unreachable and the file has stopped growing; otherwise NINA's own JPEG,
        with the last complete file as a fallback should NINA fail to render it.
        """
        newest = next(iter(self.frames), None)
        nina_name = Path(str(newest["filename"]).replace("\\", "/")).name if newest and newest.get("filename") else None
        f = self.newest_file
        announced = bool(f and nina_name and f["name"] == nina_name)
        if f and (announced or ((nina_name is None or not self.nina_status["connected"]) and f.get("stable"))):
            return {"source": "file", "path": f["path"], "name": f["name"], "token": f"f{int(f['mtime'])}-{f['size']}", "record": newest if announced else None}
        fallback = self._stable_file
        if newest is not None:
            return {"source": "nina", "index": newest["index"], "name": nina_name, "token": f"n{newest['index']}", "record": newest,
                    "fallback": fallback["path"] if fallback else None}
        if fallback:
            return {"source": "file", "path": fallback["path"], "name": fallback["name"], "token": f"f{int(fallback['mtime'])}", "record": None}
        return None

    # ------------------------------------------------------------------ guiding

    def recent_stats(self, seconds: float = adv.WINDOW_S) -> dict | None:
        now = time.time()
        samples = self.buffer.window(now - seconds, now + 1, self.dist)
        start = max(now - seconds, samples[0].t) if samples else now
        return guiding.stats(samples, self.dist.clean_seconds(start, now))

    def _phd2_dict(self) -> dict:
        return self.phd2.state.as_dict() if self.phd2 else {"connected": False}

    def _require_phd2(self) -> Phd2Client:
        if not self.phd2 or not self.phd2.connected:
            raise Phd2Disconnected("PHD2 is not connected")
        return self.phd2

    async def set_param(self, axis: str, name: str, value: float, source: str = "manual", suggestion: str | None = None) -> dict:
        phd2 = self._require_phd2()
        old = (phd2.state.params.get(axis) or {}).get(name)
        if old is None:
            raise Phd2Error("set_algo_param", 0, f"the {axis.upper()} algorithm has no parameter {name!r}")
        before = self.recent_stats()
        stored = await phd2.set_param(axis, name, value)
        self.dist.mark("parameter change", label=f"{axis.upper()} {name} {old:g}→{stored:g}")
        entry = self.advisor.record_change(axis, name, old, stored, source, before, suggestion)
        return {"stored": stored, "requested": value, "change": entry}

    async def set_exposure(self, ms: int) -> dict:
        phd2 = self._require_phd2()
        old = phd2.state.exposure_ms
        before = self.recent_stats()
        stored = await phd2.set_exposure(ms)
        self.dist.mark("parameter change", label=f"exposure {(old or 0) / 1000:g}→{stored / 1000:g} s")
        if old is not None and old != stored:
            self.advisor.record_change("both", "exposure (s)", old / 1000, stored / 1000, "manual", before)
        return {"stored": stored}

    async def set_dec_mode(self, mode: str) -> dict:
        phd2 = self._require_phd2()
        stored = await phd2.set_dec_guide_mode(mode)
        self.dist.mark("parameter change", label=f"Dec mode {stored}")
        return {"stored": stored}

    # ------------------------------------------------------------------ advisor

    def _context(self, st: dict, diag: dict) -> dict:
        mount = self.equipment.get("mount") or {}
        weather = self.equipment.get("weather") or {}
        ctx_mount = {}
        if mount.get("Connected"):
            pier = mount.get("SideOfPier")
            ctx_mount = {"altitude": mount.get("Altitude"), "pier": pier.replace("pier", "") if isinstance(pier, str) else None,
                         "flipInH": mount.get("TimeToMeridianFlip")}
        ctx_weather = {}
        if weather.get("Connected"):
            for key, label, unit in (("WindSpeed", "wind", " m/s"), ("WindGust", "gusts", " m/s"), ("CloudCover", "cloud", " %")):
                if isinstance(weather.get(key), (int, float)):
                    ctx_weather[label] = f"{weather[key]:.1f}{unit}"
        changes, _ = self.advisor.tonight()
        return {"stats": st, "diagnosis": diag, "phd2": self._phd2_dict(), "mount": ctx_mount, "weather": ctx_weather,
                "targetRms": float(self.adv_settings()["targetRms"]), "polar": self.polar.summary(),
                "changes": [c for c in changes if c["axis"] in ("ra", "dec")]}

    async def analyze(self, manual: bool) -> dict:
        return await self._ask(manual, *self._claim())

    def start_analysis(self) -> None:
        """"Analyze now": check and claim synchronously (409s reach the caller), then let the model answer in
        the background. A reasoning model can take minutes, longer than the web proxy waits; the page
        follows `advisor.busy` and the analysis log instead."""
        task = asyncio.create_task(self._ask(True, *self._claim()), name="obs-advisor-analyze")
        self._analysis_task = task  # a reference, so the task is not garbage-collected mid-call
        task.add_done_callback(_log_failure)

    def _claim(self) -> tuple[dict, dict, dict]:
        if self.advisor.busy:
            raise RuntimeError("An analysis is already running")
        s = self.adv_settings()
        st = self.recent_stats()
        diag = guiding.diagnose(st, float(s["targetRms"]))
        if not st or st["n"] < 30:
            raise RuntimeError("Not enough clean guiding yet (need about a minute of undisturbed guide steps)")
        self.advisor.busy = True
        return s, st, diag

    async def _ask(self, manual: bool, s: dict, st: dict, diag: dict) -> dict:
        entry: dict[str, Any] = {"id": f"a{int(time.time())}", "t": time.time(), "model": s["model"], "effort": s["effort"], "manual": manual,
                                 "diagnosis": diag, "stats": adv._brief(st), "suggestions": [], "notes": []}
        try:
            prompt = adv.build_prompt(self._context(st, diag))
            reply = await asyncio.to_thread(llm.call, s["model"], adv.SYSTEM, prompt, ollama_url=s["ollamaUrl"],
                                          effort=s["effort"])
            entry["latencyMs"] = reply["latencyMs"]
            parsed = llm.extract_json(reply["text"])
            if parsed is None:
                entry["error"] = f"The model's answer was not JSON ({reply['note']})."
            else:
                entry["assessment"], entry["suggestions"], entry["notes"] = adv.validate(parsed, self._phd2_dict())
            self.advisor.last_error = None
        except llm.LlmError as exc:
            entry["error"] = str(exc)
            self.advisor.last_error = str(exc)
        finally:
            self.advisor.busy = False
            self.advisor.last_call = time.time()
        self.advisor.add_analysis(entry)
        if s["autoApply"] and entry["suggestions"]:
            await self._auto_apply(entry)
        return entry

    async def _auto_apply(self, entry: dict) -> None:
        """Auto-apply: the top suggestion only, so the before/after measurement is that change's alone."""
        sg = entry["suggestions"][0]
        name = f"{sg['axis'].upper()} {sg['param']} {sg['current']:g} → {sg['value']:g}"
        if reason := self._auto_skip(sg):
            entry["auto"] = f"Not applied automatically: {reason}."
        else:
            try:
                await self.set_param(sg["axis"], sg["param"], sg["value"], source="auto", suggestion=sg["id"])
                entry["auto"] = f"Applied {name} automatically; measuring for 5 minutes (put back if it measures worse)."
            except PHD2_ERRORS as exc:
                entry["auto"] = f"Not applied automatically: {exc or type(exc).__name__}."
        self.advisor.save()

    def _auto_skip(self, sg: dict) -> str | None:
        if not self.phd2 or not self.phd2.connected or self.phd2.state.app_state != "Guiding":
            return "PHD2 is not guiding"
        if self.advisor.pending():
            return "the last change is still being measured"
        live = (self.phd2.state.params.get(sg["axis"]) or {}).get(sg["param"])
        if live is None or abs(live - sg["current"]) > 1e-6:
            return "the parameter changed since the analysis"
        changes, _ = self.advisor.tonight()
        if any(c["axis"] == sg["axis"] and c["param"] == sg["param"] and abs(c["new"] - sg["value"]) < 1e-6
               and c["verdict"] == "worse" for c in changes):
            return "the same change made guiding worse earlier tonight"
        return None

    async def _auto_revert(self) -> None:
        """With auto-apply on, the newest change, if automatic and measured WORSE, is put back (once)."""
        c = self.advisor.changes[-1] if self.advisor.changes else None
        if not c or c["source"] != "auto" or c["verdict"] != "worse" or c.get("reverted"):
            return
        c["reverted"] = True
        self.advisor.save()
        live = ((self.phd2.state.params if self.phd2 else {}).get(c["axis"]) or {}).get(c["param"])
        if live is not None and abs(live - c["new"]) < 1e-6:
            try:
                await self.set_param(c["axis"], c["param"], c["old"], source="auto-revert")
            except PHD2_ERRORS as exc:
                log.warning("auto-revert of %s.%s failed: %s", c["axis"], c["param"], exc)

    def _update_polar(self) -> None:
        calibration = self.phd2.state.calibration if self.phd2 else None
        shift = self.polar.update(self.buffer, self.dist, calibration, self.equipment.get("mount"))
        if shift:
            self.dist.events.append((shift["t"], f"polar shift {shift['deltaArcmin']:g}′"))
            log.warning("polar alignment moved: Dec drift %+.2f → %+.2f ″/min", shift["before"], shift["after"])

    async def _advisor_loop(self) -> None:
        while True:
            await asyncio.sleep(ADVISOR_TICK_S)
            try:
                self.advisor.measure(self.buffer, self.dist)
                self._update_polar()
                self.dist.prune()
                s = self.adv_settings()
                if not s["enabled"] or self.advisor.busy:
                    continue
                if s["autoApply"]:
                    await self._auto_revert()
                if not self.phd2 or self.phd2.state.app_state != "Guiding":
                    continue
                if time.time() - self.advisor.last_call < float(s["intervalMin"]) * 60:
                    continue
                if self.advisor.pending():
                    self.advisor_check = {"t": time.time(), "message": "Waiting for the last change to be measured."}
                    continue
                if self.dist.active():
                    continue
                st = self.recent_stats()
                if not st or st["n"] < guiding.MIN_SAMPLES:
                    continue
                diag = guiding.diagnose(st, float(s["targetRms"]))
                if not diag["actionable"]:
                    why = "; ".join([f"{a['axis'].upper()} {a['title'].lower()}" for a in diag["axes"]] + diag["caveats"])
                    self.advisor_check = {"t": time.time(), "message": f"Nothing to tune ({why}). The model was not asked."}
                    self.advisor.last_call = time.time()
                    continue
                self.advisor_check = None
                await self.analyze(manual=False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
                log.exception("advisor tick failed")

    # ------------------------------------------------------------------ sequence guard

    async def _guard_loop(self) -> None:
        while True:
            await asyncio.sleep(GUARD_TICK_S)
            try:
                await self.guard_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
                log.exception("sequence guard tick failed")

    async def guard_tick(self) -> None:
        g, now = self.guard, time.time()
        guiding = bool(self.phd2 and self.phd2.connected and self.phd2.state.app_state == "Guiding")
        s = self.guard_settings()
        action = g.decide(now, s, self.sequence_running(), guiding)
        if not action or not self.nina:
            return
        try:
            if action == "stop":
                await self.nina.sequence_stop()
                g.stopped(now, g.lost_seconds(now, s["stopAfterS"]), s["stopAfterS"])
                self.dist.events.append((now, "sequence stopped (guide star lost)"))
                log.warning("sequence guard: guide star lost, sequence stopped")
            else:
                await self.nina.sequence_start(skip_validation=True)
                g.started(now, now - (g.steady_since or now))
                self.dist.events.append((now, "sequence resumed"))
                log.warning("sequence guard: guiding steady again, sequence resumed")
        except NinaError as exc:
            if action == "start" and "already running" in str(exc):
                g.forget("The sequence was already running when the guard went to resume it.", now)
            else:
                g.error = f"Could not {action} the sequence: {exc}"
        except NinaUnavailable as exc:
            g.error = f"Could not {action} the sequence: NINA is not answering ({exc})"
        self._wake_history.set()  # re-read NINA's state now rather than at the next poll

    # ------------------------------------------------------------------ snapshot

    def sequence_running(self) -> bool | None:
        """For auto-ingest's night closing: None when NINA is unreachable or the state is stale."""
        ok = self.nina_status.get("lastOk")
        if not self.nina_status["connected"] or not ok or time.time() - ok > 120 or not self.sequence.get("available"):
            return None
        return self.sequence["status"] == "running"

    def _guider_card(self, st: dict | None) -> dict | None:
        p = self._phd2_dict()
        if not p.get("connected"):
            return None
        facts = []
        if st:
            facts.append({"label": "RMS (5 min)", "value": f"{st['total']:.2f}″", "advanced": False})
            facts.append({"label": "Star SNR", "value": f"{st['snr']:.0f}", "advanced": False})
        if p.get("exposureMs"):
            facts.append({"label": "Exposure", "value": f"{p['exposureMs'] / 1000:g} s", "advanced": False})
        cam = (p.get("equipment") or {}).get("camera") or {}
        if p.get("pixelScale"):
            facts.append({"label": "Pixel scale", "value": f"{p['pixelScale']:.2f}″/px", "advanced": True})
        if p.get("profile"):
            facts.append({"label": "Profile", "value": p["profile"], "advanced": True})
        activity = [a for a in self.dist.active() if a in ("dither", "settling", "star lost", "calibrating", "paused")]
        status = p.get("appState") or "—"
        if activity and status == "Guiding":
            status = f"Guiding · {activity[0]}"
        return {"key": "phd2", "label": "Guiding (PHD2)", "name": cam.get("name"), "status": status,
                "level": "warn" if "star lost" in activity or status in ("Stopped", "LostLock") else None, "facts": facts}

    def clock_offset(self) -> float:
        """NINA's clock minus ours, in seconds (0 until NINA has answered)."""
        return self.nina.clock.seconds if self.nina else 0.0

    def snapshot(self, graph_s: float = 180.0) -> dict:
        eq.CLOCK["offset"] = self.clock_offset()
        st = self.recent_stats()
        s = self.adv_settings()
        cards = eq.cards(self.equipment) if self.nina_status["connected"] else []
        guider = self._guider_card(st)
        if guider:
            cards = [c for c in cards if c["key"] != "guider"]
            cards.insert(2 if len(cards) >= 2 else len(cards), guider)
        changes, analyses = self.advisor.tonight()
        host, port = self.phd2_addr()
        latest = self.latest_frame()
        cam = self.equipment.get("camera") or {}
        left = eq._seconds_until(cam.get("ExposureEndTime")) if cam.get("Connected") and cam.get("IsExposing") else None
        total = ((self.sequence.get("current") or {}).get("info") or {}).get("ExposureTime")
        # Always present while the camera is connected (left is None between frames), so the page keeps its layout.
        exposure = None
        if cam.get("Connected"):
            cam_state = str(cam.get("CameraState") or ("Exposing" if cam.get("IsExposing") else "Idle"))
            exposure = {"left": round(left, 1) if left is not None and left > 0 else None, "total": total, "state": cam_state}
        return {
            "exposure": exposure,
            "simulated": self.simulated,
            "serverTime": time.time(),
            "nina": {**self.nina_status, "websocket": self.ws_connected, "clockOffset": round(self.clock_offset(), 1)},
            "phd2": {**self._phd2_dict(), "address": f"{host}:{port}", "error": None if self.phd2 and self.phd2.connected else self.phd2_error},
            "equipment": cards,
            "lost": [{"key": k, "label": eq.LABELS.get(k, k), "name": self._seen.get(k), "since": t} for k, t in self._lost.items()],
            "sequence": self.sequence,
            "frames": self.frames[:60],
            "latest": {k: v for k, v in latest.items() if k not in ("path", "fallback")} if latest else None,
            "guiding": {
                "stats": st,
                "diagnosis": guiding.diagnose(st, float(s["targetRms"])),
                "graph": self.buffer.series(graph_s, self.dist),
                "active": self.dist.active(),
                "scale": self.buffer.scale,
            },
            "advisor": {
                "enabled": bool(s["enabled"]), "model": s["model"], "effort": s["effort"], "autoApply": bool(s["autoApply"]), "intervalMin": s["intervalMin"], "targetRms": s["targetRms"],
                "key": llm.key_status(s["model"], s["ollamaUrl"]), "busy": self.advisor.busy, "lastError": self.advisor.last_error,
                "check": self.advisor_check, "analyses": list(reversed(analyses[-10:])), "changes": list(reversed(changes)),
            },
            "polar": self.polar.summary(),
            "guard": self.guard.as_dict(time.time(), self.guard_settings()),
            "paramInfo": {k: {"label": v[0], "help": v[1], "basic": k in adv.BASIC_PARAMS} for k, v in adv.PARAM_INFO.items()},
        }
