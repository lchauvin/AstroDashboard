"""Asyncio client for the PHD2 event server (TCP 4400 + instance − 1).

One socket carries two interleaved streams of CRLF-terminated JSON objects:
event notifications (an `Event` key, pushed by PHD2) and JSON-RPC 2.0 responses
(an `id` matching a request we sent). The reader loop demultiplexes on that.

`guide` and `dither` answer as soon as settling *starts*; they complete with a
later `SettleDone` event, which the dashboard simply observes — it never waits.

PHD2 has no authentication and binds every interface: keep it on the LAN.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


class Phd2Error(RuntimeError):
    def __init__(self, method: str, code: Any, message: str) -> None:
        super().__init__(f"{method}: {message}")
        self.method, self.code, self.message = method, code, message


class Phd2Disconnected(ConnectionError):
    pass


@dataclass
class Phd2State:
    connected: bool = False
    version: str | None = None
    app_state: str = "Unknown"
    pixel_scale: float | None = None
    exposure_ms: int | None = None
    exposure_choices: list[int] = field(default_factory=list)
    dec_guide_mode: str | None = None
    calibrated: bool | None = None
    calibration: dict | None = None
    profile: str | None = None
    equipment: dict = field(default_factory=dict)
    lock_position: list[float] | None = None
    algorithms: dict[str, str] = field(default_factory=dict)
    """Axis → algorithm name ("Hysteresis", "ResistSwitch", "Lowpass2", "Predictive PEC" …)."""
    params: dict[str, dict[str, float]] = field(default_factory=dict)
    """Axis → {param name → value}, only the numeric parameters the algorithm exposes."""
    settling: bool = False
    paused: bool = False
    guiding_since: float | None = None
    last_alert: dict | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "version": self.version,
            "appState": self.app_state,
            "pixelScale": self.pixel_scale,
            "exposureMs": self.exposure_ms,
            "exposureChoices": self.exposure_choices,
            "decGuideMode": self.dec_guide_mode,
            "calibrated": self.calibrated,
            "calibration": self.calibration,
            "profile": self.profile,
            "equipment": self.equipment,
            "lockPosition": self.lock_position,
            "algorithms": self.algorithms,
            "params": self.params,
            "settling": self.settling,
            "paused": self.paused,
            "guidingForS": round(time.time() - self.guiding_since) if self.guiding_since else None,
            "lastAlert": self.last_alert,
            "error": self.error,
        }


class Phd2Client:
    """One connection. `run()` connects, seeds state and reads until the socket drops (then raises)."""

    def __init__(self, host: str, port: int, *, on_event: Callable[[dict], None] | None = None,
                 on_algorithms: Callable[[dict, dict], None] | None = None,
                 connect_timeout: float = 5.0, rpc_timeout: float = 10.0) -> None:
        """`on_algorithms(old, new)`: an axis's algorithm changed while connected (in PHD2's Brain)."""
        self.host, self.port = host, port
        self.connect_timeout, self.rpc_timeout = connect_timeout, rpc_timeout
        self.state = Phd2State()
        self._on_event = on_event
        self._on_algorithms = on_algorithms
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self._lock = asyncio.Lock()
        self._params_lock = asyncio.Lock()  # one ConfigurationChange burst queues several refreshes: one at a time
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def run(self) -> None:
        # get_star_image is one ~100 KB base64 line: far over StreamReader's 64 KB default.
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, limit=1024 * 1024), timeout=self.connect_timeout)
        self.state = Phd2State(connected=True)
        log.info("PHD2 connected at %s:%d", self.host, self.port)
        reader = asyncio.create_task(self._read_loop(), name="phd2-reader")
        beat = asyncio.create_task(self._heartbeat(), name="phd2-heartbeat")
        try:
            await self.refresh()
            await reader
        finally:
            beat.cancel()
            self._fail_pending(Phd2Disconnected("PHD2 connection closed"))
            reader.cancel()
            with contextlib.suppress(BaseException):
                await reader
            writer, self._writer, self._reader = self._writer, None, None
            self.state.connected = False
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            log.info("PHD2 disconnected")

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _heartbeat(self) -> None:
        """Re-read the app state every 15 s: proves the request path works and corrects missed events."""
        while True:
            await asyncio.sleep(15)
            with contextlib.suppress(Exception):
                self._set_app_state(str(await self.call("get_app_state", timeout=5)))

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            raw = await self._reader.readline()
            if not raw:
                raise Phd2Disconnected("PHD2 closed the connection")
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if "Event" in msg:
                self._handle_event(msg)
            elif "id" in msg:
                fut = self._pending.pop(msg.get("id"), None)
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    err = msg.get("error") or {}
                    fut.set_exception(Phd2Error(getattr(fut, "method", "?"), err.get("code"), err.get("message", "error")))
                else:
                    fut.set_result(msg.get("result"))

    def _set_app_state(self, state: str) -> None:
        if state == self.state.app_state:
            return
        self.state.app_state = state
        if state == "Guiding":
            self.state.guiding_since = self.state.guiding_since or time.time()
        else:
            self.state.guiding_since = None

    def _handle_event(self, msg: dict) -> None:
        ev = msg.get("Event", "")
        st = self.state
        if ev == "Version":
            st.version = msg.get("PHDVersion")
        elif ev == "AppState":
            self._set_app_state(msg.get("State", "Unknown"))
        elif ev == "StartGuiding":
            self._set_app_state("Guiding")
        elif ev == "GuidingStopped":
            self._set_app_state("Stopped")
        elif ev == "StartCalibration":
            self._set_app_state("Calibrating")
        elif ev in ("CalibrationComplete", "CalibrationFailed"):
            asyncio.create_task(self._safe(self._refresh_calibration()))
        elif ev == "Paused":
            st.paused = True
            self._set_app_state("Paused")
        elif ev == "Resumed":
            st.paused = False
        elif ev in ("SettleBegin", "Settling"):
            st.settling = True
        elif ev == "SettleDone":
            st.settling = False
        elif ev == "LockPositionSet":
            st.lock_position = [msg.get("X"), msg.get("Y")]
        elif ev == "Alert":
            st.last_alert = {"type": msg.get("Type"), "msg": msg.get("Msg"), "at": time.time()}
        elif ev == "ConfigurationChange":
            asyncio.create_task(self._safe(self.refresh_params()))
        elif ev == "GuideStep":
            if st.app_state != "Guiding":
                self._set_app_state("Guiding")
            if not st.params:
                # The first seed can race PHD2's startup (the algorithms are not wired yet).
                asyncio.create_task(self._safe(self.refresh_params()))
        if self._on_event:
            try:
                self._on_event(msg)
            except Exception:  # noqa: BLE001 — a bad handler must not kill the reader
                log.exception("PHD2 event handler failed for %s", ev)

    async def call(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        if not self.connected:
            raise Phd2Disconnected(f"cannot call {method}: PHD2 not connected")
        req_id = next(self._ids)
        payload: dict[str, Any] = {"method": method, "id": req_id, "jsonrpc": "2.0"}
        if params is not None:
            payload["params"] = params
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        fut.method = method  # type: ignore[attr-defined]
        self._pending[req_id] = fut
        try:
            async with self._lock:
                assert self._writer is not None
                self._writer.write((json.dumps(payload) + "\r\n").encode())
                await self._writer.drain()
            return await asyncio.wait_for(fut, timeout or self.rpc_timeout)
        except asyncio.TimeoutError as exc:
            raise Phd2Disconnected(f"{method}: no answer from PHD2 in {timeout or self.rpc_timeout:.0f} s") from exc
        finally:
            self._pending.pop(req_id, None)

    @staticmethod
    async def _safe(coro) -> None:
        try:
            await coro
        except Exception as exc:  # noqa: BLE001
            log.debug("PHD2 refresh failed: %s", exc)

    # ---------------------------------------------------------------- state

    async def refresh(self) -> None:
        """Everything events alone never provide. Called on every (re)connect."""
        st = self.state

        async def get(method: str, params: Any = None):
            try:
                return await self.call(method, params)
            except (Phd2Error, Phd2Disconnected):
                return None

        if (v := await get("get_app_state")) is not None:
            self._set_app_state(str(v))
        if (v := await get("get_pixel_scale")):
            st.pixel_scale = float(v)
        if (v := await get("get_exposure")) is not None:
            st.exposure_ms = int(v)
        if isinstance(v := await get("get_exposure_durations"), list):
            st.exposure_choices = [int(x) for x in v if isinstance(x, (int, float))]
        st.dec_guide_mode = await get("get_dec_guide_mode")
        st.paused = bool(await get("get_paused"))
        if isinstance(v := await get("get_profile"), dict):
            st.profile = v.get("name")
        if isinstance(v := await get("get_current_equipment"), dict):
            st.equipment = {k: {"name": d.get("name"), "connected": d.get("connected")} for k, d in v.items() if isinstance(d, dict)}
        if isinstance(v := await get("get_lock_position"), list):
            st.lock_position = v
        await self._refresh_calibration()
        await self.refresh_params()

    async def _refresh_calibration(self) -> None:
        try:
            self.state.calibrated = bool(await self.call("get_calibrated"))
            data = await self.call("get_calibration_data", ["Mount"]) if self.state.calibrated else None
            self.state.calibration = data if isinstance(data, dict) else None
        except (Phd2Error, Phd2Disconnected):
            pass

    async def refresh_params(self) -> None:
        async with self._params_lock:
            old = self.state.algorithms
            await self._read_params()
            new = self.state.algorithms
        if old and new and old != new and self._on_algorithms:
            try:
                self._on_algorithms(old, new)
            except Exception:  # noqa: BLE001 — like a bad event handler
                log.exception("PHD2 algorithm-change handler failed")

    async def _read_params(self) -> None:
        params: dict[str, dict[str, float]] = {}
        algos: dict[str, str] = {}
        for axis in ("ra", "dec"):
            try:
                names = await self.call("get_algo_param_names", [axis]) or []
            except (Phd2Error, Phd2Disconnected):
                continue
            params[axis] = {}
            for name in names:
                try:
                    value = await self.call("get_algo_param", [axis, name])
                except (Phd2Error, Phd2Disconnected):
                    continue
                # "algorithmName" answers with a string; everything else is numeric.
                if isinstance(value, str):
                    if name.lower().startswith("algorithm"):
                        algos[axis] = value
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    params[axis][name] = float(value)
        self.state.params, self.state.algorithms = params, algos

    # ---------------------------------------------------------------- actions

    async def set_param(self, axis: str, name: str, value: float) -> float:
        """Set a guiding parameter; returns what PHD2 actually stored (it clamps silently)."""
        await self.call("set_algo_param", [axis, name, value])
        stored = float(await self.call("get_algo_param", [axis, name]))
        self.state.params.setdefault(axis, {})[name] = stored
        return stored

    async def set_exposure(self, ms: int) -> int:
        await self.call("set_exposure", [int(ms)])
        self.state.exposure_ms = int(await self.call("get_exposure"))
        return self.state.exposure_ms

    async def set_dec_guide_mode(self, mode: str) -> str:
        await self.call("set_dec_guide_mode", [mode])
        self.state.dec_guide_mode = await self.call("get_dec_guide_mode")
        return str(self.state.dec_guide_mode)

    async def guide(self, recalibrate: bool = False) -> None:
        await self.call("guide", {"settle": {"pixels": 1.5, "time": 8, "timeout": 40}, "recalibrate": recalibrate})

    async def dither(self, pixels: float = 3.0, ra_only: bool = False) -> None:
        await self.call("dither", {"amount": pixels, "raOnly": ra_only, "settle": {"pixels": 1.5, "time": 8, "timeout": 40}})

    async def stop(self) -> None:
        await self.call("stop_capture")

    async def set_paused(self, paused: bool) -> None:
        await self.call("set_paused", [paused])
        self.state.paused = paused

    async def star_image(self, size: int = 31) -> dict:
        result = await self.call("get_star_image", {"size": max(15, int(size))})
        if not isinstance(result, dict):
            raise Phd2Error("get_star_image", 0, "unexpected answer")
        return result
