"""A simulated observatory for daylight testing: `uv run astrodash-engine --fake-observatory`.

* Fake PHD2 speaks the real wire protocol on a local TCP port (JSON lines, events + JSON-RPC).
* Fake NINA is a router mounted on the engine itself under /fake-nina (REST + websocket).

The mount responds to the guiding parameters, so the diagnosis and the advisor have something
real to react to: RA starts too aggressive (it rings), Dec has a deadband too wide for its drift.
The Dec drift is a polar axis 8′ too high and 15′ west of the pole, at the sky's real hour angle,
for the polar-alignment estimate to find (in windows of 5 minutes; the fit needs hours).
Seeing wanders, dithers happen every few minutes, the star is occasionally lost, a frame is
"saved" every 40 s. Nothing is written to disk: frame previews are rendered in memory.
Fake NINA's safety monitor is the dashboard's own sky monitor, and its sequence saves no frame
while that says unsafe (as Loop While Safe + Loop While Unsafe would); /fake-nina/v2/api/sim/cloud
and /sim/haze bring the weather.

No `from __future__ import annotations` here: FastAPI must resolve the WebSocket annotation.
"""

import asyncio
import io
import json
import logging
import math
import random
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

log = logging.getLogger(__name__)

FRAME_EVERY_S = 40.0
EXPOSURE_S = 40.0
GAIN = 1.35  # RA response to a pulse: > 1 means the calibration under-estimates the guide rate
X_RATE, Y_RATE = 3.912, 3.705  # calibrated guide rates, px/s
PA_ALT, PA_AZ, LATITUDE = 8.0, -15.0, 45.5  # polar axis error (′, + = too high / east of the pole) and site
RA_H, DEC_DEG, LST0_H = 0.7122, 41.27, 23.3006  # M 31, and the sidereal time when the simulator starts
FILTERS = ("L", "R", "G", "B", "Ha", "OIII", "SII")


class Sim:
    def __init__(self) -> None:
        self.scale = 1.6
        self.seeing = 2.0
        self.params = {
            "ra": {"minMove": 0.15, "hysteresis": 0.10, "aggression": 0.95},
            "dec": {"minMove": 0.45, "aggression": 0.70, "fastSwitch": 1.0},
        }
        self.algorithms = {"ra": "Hysteresis", "dec": "ResistSwitch"}
        self.exposure_ms = 2000
        self.dec_mode = "Auto"
        self.state = "Guiding"
        self.frame = 0
        self.started = time.time()
        self.ra_err = 0.0
        self.dec_err = 0.0
        self.prev_ra = 0.0
        self.target, self.filter = "M 31", "Ha"
        self.focus = 18500
        self.images: list[dict] = []
        self.exposure_started = time.time()
        self.sequence_running = True
        self.cloud_until = 0.0  # /fake-nina/v2/api/sim/cloud: the guide star is lost until then
        self.haze_until, self.haze = 0.0, 1.0  # /fake-nina/v2/api/sim/haze: the guide star dimmed to `haze` until then
        self.safe: Optional[Callable[[], bool]] = None  # the dashboard's sky monitor, as NINA's safety monitor

    def step(self) -> dict:
        self.frame += 1
        self.seeing = max(0.9, min(4.5, self.seeing + random.gauss(0, 0.03)))
        noise = self.seeing * 0.24
        t = time.time() - self.started
        self.ra_err += random.gauss(0, noise) + 0.10 * math.sin(t / 70)
        # A positive residual is corrected northwards: it is the pointing lying south of the lock.
        self.dec_err += random.gauss(0, noise * 0.8) - self.polar_drift() * self.exposure_ms / 60000
        jitter = self.seeing * 0.16
        ra_m, dec_m = self.ra_err + random.gauss(0, jitter), self.dec_err + random.gauss(0, jitter)

        def correction(axis: str, measured: float, prev: float) -> float:
            p = self.params[axis]
            if abs(measured) < p["minMove"] * self.scale:
                return 0.0
            h = p.get("hysteresis", 0.0)
            return p["aggression"] * measured * (1 - h) + h * prev

        ra_c = correction("ra", ra_m, self.prev_ra)
        dec_c = correction("dec", dec_m, 0.0) if self.dec_mode != "Off" else 0.0
        self.prev_ra = ra_c
        self.ra_err -= ra_c * GAIN
        self.dec_err -= dec_c
        ms = lambda c, rate: round(abs(c) / self.scale / rate * 1000)  # noqa: E731
        dim = self.haze if time.time() < self.haze_until else 1.0
        return {
            "Event": "GuideStep", "Timestamp": time.time(), "Host": "sim", "Inst": 1, "Frame": self.frame, "Time": t,
            "Mount": "Sim Mount", "dx": ra_m / self.scale, "dy": dec_m / self.scale,
            "RADistanceRaw": ra_m / self.scale, "DECDistanceRaw": dec_m / self.scale,
            "RADuration": ms(ra_c, X_RATE), "RADirection": "East" if ra_c > 0 else "West",
            "DECDuration": ms(dec_c, Y_RATE), "DECDirection": "North" if dec_c > 0 else "South",
            "StarMass": max(100.0, (5200 + random.gauss(0, 260)) * dim),
            "SNR": max(3.0, (26 - (self.seeing - 2.0) * 5 + random.gauss(0, 1.5)) * dim),
            "HFD": self.seeing / self.scale + random.gauss(0, 0.05),
            "AvgDist": math.hypot(ra_m, dec_m) / self.scale, "RALimited": False, "DecLimited": False,
        }

    def lst(self) -> float:
        return (LST0_H + (time.time() - self.started) / 3600 * 1.0027379) % 24

    def polar_drift(self) -> float:
        """The pointing's Dec drift from the polar error, ″/min, + = north (see polar.py)."""
        h = (((self.lst() - RA_H + 12) % 24) - 12) * math.pi / 12
        k = 60 * 2 * math.pi / 1436.07
        return k * (PA_AZ * math.cos(math.radians(LATITUDE)) * math.cos(h) - PA_ALT * math.sin(h))

    def star(self, size: int) -> Optional[dict]:
        import base64

        import numpy as np

        if self.state not in ("Guiding", "Looping", "Paused"):
            return None
        size = min(max(15, size), 63)
        sigma = max(0.9, self.seeing / self.scale / 2.355)
        c = (size - 1) / 2
        x0 = c + max(-3.0, min(3.0, self.ra_err / self.scale))
        y0 = c + max(-3.0, min(3.0, self.dec_err / self.scale))
        ys, xs = np.mgrid[0:size, 0:size]
        img = 900 + 22000 * min(1, 2.6 / self.seeing) * np.exp(-((xs - x0) ** 2 + (ys - y0) ** 2) / (2 * sigma**2))
        img += np.random.normal(0, 55, (size, size))
        px = np.clip(img, 0, 65535).astype("<u2")
        return {"frame": self.frame, "width": size, "height": size, "star_pos": [x0, y0],
                "pixels": base64.b64encode(px.tobytes()).decode()}


class FakePhd2:
    def __init__(self, sim: Sim) -> None:
        self.sim = sim
        self.port = 0
        self.clients: list[asyncio.StreamWriter] = []
        self._server: Optional[asyncio.base_events.Server] = None
        self._pump: Optional[asyncio.Task] = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self._pump = asyncio.create_task(self._run(), name="fake-phd2")
        log.info("fake PHD2 on 127.0.0.1:%d", self.port)
        return self.port

    async def stop(self) -> None:
        if self._pump:
            self._pump.cancel()
        for w in self.clients:
            w.close()
        if self._server:
            self._server.close()

    async def _send(self, w: asyncio.StreamWriter, payload: dict) -> None:
        w.write((json.dumps(payload) + "\r\n").encode())
        await w.drain()

    async def broadcast(self, payload: dict) -> None:
        for w in list(self.clients):
            try:
                await self._send(w, payload)
            except Exception:  # noqa: BLE001
                self.clients.remove(w)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.append(writer)
        await self._send(writer, {"Event": "Version", "PHDVersion": "2.6.13-sim", "MsgVersion": 1})
        await self._send(writer, {"Event": "AppState", "State": self.sim.state})
        try:
            while raw := await reader.readline():
                try:
                    req = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._send(writer, self._respond(req))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if writer in self.clients:
                self.clients.remove(writer)

    def _respond(self, req: dict) -> dict:
        m, p, s = req.get("method"), req.get("params") or [], self.sim
        rid = req.get("id")

        def err(msg: str) -> dict:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": 1, "message": msg}}

        r: Any = 0
        if m == "get_app_state":
            r = s.state
        elif m == "get_pixel_scale":
            r = s.scale
        elif m == "get_exposure":
            r = s.exposure_ms
        elif m == "get_exposure_durations":
            r = [500, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000]
        elif m == "set_exposure":
            s.exposure_ms = int(p[0])
        elif m == "get_dec_guide_mode":
            r = s.dec_mode
        elif m == "set_dec_guide_mode":
            s.dec_mode = str(p[0])
        elif m == "get_paused":
            r = s.state == "Paused"
        elif m == "get_profile":
            r = {"id": 1, "name": "Sim guide rig"}
        elif m == "get_current_equipment":
            r = {"camera": {"name": "Sim ASI120MM Mini", "connected": True}, "mount": {"name": "Sim EQ6-R", "connected": True}}
        elif m == "get_lock_position":
            r = [320.4, 241.7]
        elif m == "get_calibrated":
            r = True
        elif m == "get_calibration_data":
            r = {"xAngle": 87.3, "yAngle": -2.1, "xRate": X_RATE, "yRate": Y_RATE, "xParity": "+", "yParity": "+", "declination": 41.0}
        elif m == "get_algo_param_names":
            axis = p[0] if p else "ra"
            r = ["algorithmName", *s.params[axis]]
        elif m == "get_algo_param":
            axis, name = p[0], p[1]
            if name == "algorithmName":
                r = s.algorithms[axis]
            elif name in s.params.get(axis, {}):
                r = s.params[axis][name]
            else:
                return err(f"invalid param name {name}")
        elif m == "set_algo_param":
            axis, name, value = p[0], p[1], float(p[2])
            if name not in s.params.get(axis, {}):
                return err(f"invalid param name {name}")
            s.params[axis][name] = max(0.0, min(value, 1.0 if name != "minMove" else 5.0))
            log.info("[sim] %s.%s = %g", axis, name, s.params[axis][name])
        elif m == "guide":
            s.state = "Guiding"
            asyncio.create_task(self._settle(False))
        elif m == "dither":
            asyncio.create_task(self._settle(True))
        elif m == "stop_capture":
            s.state = "Stopped"
            asyncio.create_task(self.broadcast({"Event": "GuidingStopped"}))
        elif m == "set_paused":
            paused = bool(p[0]) if p else False
            s.state = "Paused" if paused else "Guiding"
            asyncio.create_task(self.broadcast({"Event": "Paused" if paused else "Resumed"}))
        elif m == "get_star_image":
            size = int(p.get("size", 15)) if isinstance(p, dict) else int(p[0]) if p else 15
            r = s.star(size)
            if r is None:
                return err("cannot get star image when not looping")
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {m}"}}
        return {"jsonrpc": "2.0", "id": rid, "result": r}

    async def _settle(self, dithered: bool) -> None:
        if dithered:
            self.sim.ra_err += random.uniform(-4, 4)
            self.sim.dec_err += random.uniform(-4, 4)
            await self.broadcast({"Event": "GuidingDithered", "dx": 3.0, "dy": 3.0})
        await self.broadcast({"Event": "SettleBegin"})
        await asyncio.sleep(8)
        await self.broadcast({"Event": "SettleDone", "Status": 0, "TotalFrames": 6, "DroppedFrames": 0})

    async def _run(self) -> None:
        next_dither = time.time() + 300
        while True:
            await asyncio.sleep(self.sim.exposure_ms / 1000)
            if self.sim.state != "Guiding":
                continue
            if time.time() < self.sim.cloud_until:
                await self.broadcast({"Event": "StarLost", "Frame": self.sim.frame, "SNR": 1.2, "Status": "star lost - low SNR"})
                continue
            await self.broadcast(self.sim.step())
            if time.time() >= next_dither:
                next_dither = time.time() + random.uniform(260, 360)
                await self._settle(True)
            elif random.random() < 0.002:
                await self.broadcast({"Event": "StarLost", "Frame": self.sim.frame, "SNR": 2.1, "Status": "star lost - low SNR"})


def nina_router(sim: Sim) -> APIRouter:
    """The parts of the NINA Advanced API the dashboard reads, under /fake-nina/v2/…"""
    router = APIRouter(prefix="/fake-nina/v2")
    sockets: list[WebSocket] = []

    def ok(payload: Any) -> dict:
        return {"Response": payload, "Error": "", "StatusCode": 200, "Success": True, "Type": "API"}

    def stats(i: int) -> dict:
        clouded = random.random() < 0.1
        return {
            "Index": i, "ExposureTime": EXPOSURE_S, "ImageType": "LIGHT", "Filter": sim.filter, "TargetName": sim.target,
            "Date": datetime.now().astimezone().isoformat(timespec="seconds"), "Temperature": -10.0, "Gain": 100, "Offset": 50,
            "CameraName": "Sim ASI2600MM", "FocalLength": 800.0, "Mean": 980.0, "Median": 970.0, "StDev": 110.0,
            "Stars": int((240 if clouded else 820) * random.uniform(0.9, 1.1)),
            "HFR": sim.seeing * (1.5 if clouded else 1.05) * random.uniform(0.97, 1.03), "HFRStDev": 0.4,
            "RmsText": f"{sim.seeing * 0.3:.2f}", "Min": 120.0, "Max": 48000.0,
            "Filename": f"D:\\Astro\\.Download\\{datetime.now():%Y-%m-%d}\\{sim.target}\\LIGHT\\{sim.filter}\\sim_{i:04d}.fits",
        }

    for i in range(6):  # some history so the page is not empty on first load
        sim.images.append(stats(i))

    @router.get("/api/version")
    async def version():
        return ok("2.2.0-sim")

    @router.get("/api/sequence/json")
    async def sequence():
        n = len(sim.images)
        run = "RUNNING" if sim.sequence_running else "CREATED"
        return ok([
            {"Name": "Start", "Status": "FINISHED", "Items": [{"Name": "Cool Camera", "Status": "FINISHED"}, {"Name": "Unpark Scope", "Status": "FINISHED"}]},
            {"Name": "Targets", "Status": run, "Items": [
                {"Name": sim.target, "Status": run, "Conditions": [{"Name": "Loop Until Time"}], "Items": [
                    {"Name": "Slew and Center", "Status": "FINISHED"},
                    {"Name": "Run Autofocus", "Status": "FINISHED"},
                    {"Name": "Smart Exposure", "Status": run, "ExposureTime": EXPOSURE_S, "Filter": {"Name": sim.filter},
                     "Gain": 100, "Offset": 50, "ImageType": "LIGHT",
                     "Conditions": [{"Name": "Loop For Iterations", "Iterations": 60, "CompletedIterations": min(n, 60)}]},
                    {"Name": "Smart Exposure", "Status": "CREATED", "ExposureTime": EXPOSURE_S, "Filter": {"Name": "OIII"},
                     "Conditions": [{"Name": "Loop For Iterations", "Iterations": 60, "CompletedIterations": 0}]},
                ]},
            ]},
            {"Name": "End", "Status": "CREATED", "Items": [{"Name": "Warm Camera", "Status": "CREATED"}, {"Name": "Park Scope", "Status": "CREATED"}]},
            {"GlobalTriggers": [{"Name": "Meridian Flip"}]},
        ])

    @router.get("/api/sim/cloud")
    async def sim_cloud(seconds: float = 120):
        """Not NINA: a cloud for the simulator. The guide star is lost for `seconds`."""
        sim.cloud_until = time.time() + seconds
        return ok(f"cloud for {seconds:g} s")

    @router.get("/api/sim/haze")
    async def sim_haze(seconds: float = 300, pct: float = 35):
        """Not NINA: thin cloud for the simulator. PHD2 keeps its star, at `pct` % of its brightness, for `seconds`."""
        sim.haze_until, sim.haze = time.time() + seconds, max(0.01, min(1.0, pct / 100))
        return ok(f"haze at {pct:g} % for {seconds:g} s")

    @router.get("/api/sequence/state")
    async def sequence_state():
        # Only the target container's Target matters to the dashboard.
        return ok([{"Name": "Start_Container", "Status": "FINISHED", "Items": []},
                   {"Name": "Targets_Container", "Status": "RUNNING", "Items": [
                       {"Name": f"{sim.target}_Container", "Status": "RUNNING", "Target": {"TargetName": sim.target}, "Items": []}]}])

    @router.get("/api/equipment/{device}/info")
    async def equipment(device: str):
        if device in ("dome", "switch", "flatdevice"):
            return {"Response": None, "Error": f"{device} not connected", "StatusCode": 409, "Success": False, "Type": "API"}
        base: dict[str, Any] = {"Connected": True, "Name": f"Sim {device}"}
        elapsed = time.time() - sim.exposure_started
        if device == "camera":
            end = datetime.now() + timedelta(seconds=max(0.0, EXPOSURE_S - elapsed))
            base.update(Name="Sim ASI2600MM", Temperature=-9.8 + random.gauss(0, 0.1), TemperatureSetPoint=-10.0, CoolerOn=True,
                        CoolerPower=41.0 + random.gauss(0, 1), IsExposing=True, CameraState="Exposing", ExposureEndTime=end.isoformat(),
                        Gain=100, Offset=50, BinX=1, BinY=1, ReadoutModes=["Normal"], ReadoutMode=0, LastDownloadTime=1.8, DewHeaterOn=True)
        elif device == "mount":
            base.update(Name="Sim EQ6-R", Altitude=58.2, Azimuth=141.9, SideOfPier="pierWest", TimeToMeridianFlip=1.42,
                        TrackingEnabled=True, AtPark=False, Slewing=False, RightAscensionString="00:42:44", DeclinationString="+41° 16' 09\"",
                        SiderealTimeString="23:18:02", TrackingRate={"TrackingMode": "Sidereal"},
                        SiderealTime=sim.lst(), RightAscension=RA_H, Declination=DEC_DEG, SiteLatitude=LATITUDE)
        elif device == "focuser":
            base.update(Name="Sim EAF", Position=sim.focus, Temperature=6.4, IsMoving=False, StepSize=4.5, TempComp=False)
        elif device == "filterwheel":
            base.update(Name="Sim EFW", SelectedFilter={"Name": sim.filter, "Id": FILTERS.index(sim.filter)}, IsMoving=False,
                        AvailableFilters=[{"Name": n, "Id": i} for i, n in enumerate(FILTERS)])
        elif device == "rotator":
            base.update(Connected=False)
        elif device == "guider":
            base.update(Name="PHD2", State=sim.state)
        elif device == "weather":
            base.update(Name="Sim weather", Temperature=7.2, DewPoint=3.1, Humidity=75.0, CloudCover=8.0, WindSpeed=2.4, SkyQuality=20.1)
        elif device == "safetymonitor":
            base.update(Name="AstroDashboard sky monitor", Connected=sim.safe is not None, IsSafe=bool(sim.safe and sim.safe()))
        return ok(base)

    @router.get("/api/image-history")
    async def history(all: str = "true", count: str = "false"):  # noqa: A002
        return ok(len(sim.images) if count == "true" else sim.images)

    @router.get("/api/image/{index}")
    async def image(index: int, scale: float = 0.5):
        return Response(_jpeg(index, sim.seeing), media_type="image/jpeg")

    @router.websocket("/socket")
    async def socket(ws: WebSocket):
        await ws.accept()
        sockets.append(ws)
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, Exception):  # noqa: BLE001
            pass
        finally:
            if ws in sockets:
                sockets.remove(ws)

    async def emit() -> None:
        while True:
            await asyncio.sleep(FRAME_EVERY_S)
            if not sim.sequence_running or (sim.safe and not sim.safe()):
                continue  # Loop While Safe has interrupted the exposure; Loop While Unsafe waits
            sim.exposure_started = time.time()
            rec = stats(len(sim.images))
            sim.images.append(rec)
            sim.focus += random.choice((-3, 0, 0, 2))
            for ws in list(sockets):
                try:
                    await ws.send_json({"Response": {"Event": "IMAGE-SAVE", "ImageStatistics": rec}, "Success": True, "Type": "Socket"})
                except Exception:  # noqa: BLE001
                    sockets.remove(ws)

    router.emit = emit  # type: ignore[attr-defined]
    return router


_JPEG_CACHE: dict[int, bytes] = {}


def _jpeg(index: int, seeing: float) -> bytes:
    """A synthetic star field with a soft nebula, rendered once per frame index."""
    if index in _JPEG_CACHE:
        return _JPEG_CACHE[index]
    import numpy as np
    from PIL import Image

    h, w = 600, 900
    rng = np.random.default_rng(7)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 20 + 6 * xs / w + 40 * np.exp(-(((xs - w * 0.6) ** 2) / 30000 + ((ys - h * 0.45) ** 2) / 14000))
    sigma = max(0.8, seeing * 0.55)
    for x0, y0, peak in zip(rng.uniform(0, w, 260), rng.uniform(0, h, 260), rng.pareto(1.5, 260) * 60 + 30):
        x1, x2, y1, y2 = int(max(0, x0 - 10)), int(min(w, x0 + 11)), int(max(0, y0 - 10)), int(min(h, y0 + 11))
        img[y1:y2, x1:x2] += peak * np.exp(-((xs[y1:y2, x1:x2] - x0) ** 2 + (ys[y1:y2, x1:x2] - y0) ** 2) / (2 * sigma**2))
    img += np.random.default_rng(index).normal(0, 3, (h, w))
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=85)
    _JPEG_CACHE[index] = buf.getvalue()
    if len(_JPEG_CACHE) > 20:
        _JPEG_CACHE.pop(next(iter(_JPEG_CACHE)))
    return _JPEG_CACHE[index]


