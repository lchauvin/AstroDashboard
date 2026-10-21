"""The sky monitor as an ASCOM Alpaca SafetyMonitor, for NINA (Equipment › Safety Monitor).

Its own port (`[alpaca] port`, 3008), bound to every interface: NINA runs on the observatory PC. Only this device is
served there, never the engine's API. Its own thread and event loop too, so NINA's polls never wait on the engine.
UDP discovery (port 32227) lets NINA find it without typing an address.

The device only reads `SkyMonitor.safe`; nothing NINA sends changes the verdict. `Connected` is the device's
(one for every client, as most Alpaca drivers do), and IsSafe is false while not connected, as ASCOM asks.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import __version__
from .sky import SkyMonitor

log = logging.getLogger(__name__)

DISCOVERY_PORT = 32227
DEVICE_NAME = "AstroDashboard sky monitor"
UNIQUE_ID = "3f6d2b8e-6c1a-4f2e-9a57-0d4c8e5b7a21"
INTERFACE_VERSION = 3  # ISafetyMonitorV3 (ASCOM Platform 7): Connect/Disconnect/Connecting/DeviceState too
NOT_IMPLEMENTED = 0x400
_NO_VALUE = object()


def create_app(monitor: SkyMonitor) -> FastAPI:
    app = FastAPI(title=DEVICE_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    device = {"connected": False}
    server_tx = itertools.count(1)

    async def params(request: Request) -> dict[str, str]:
        """The query string (GET) or form body (PUT), names lower-cased: Alpaca clients differ in case."""
        items = dict(request.query_params)
        if request.method == "PUT":
            body = (await request.body()).decode("utf-8", "replace")
            items.update({k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()})
        return {k.lower(): v for k, v in items.items()}

    def reply(p: dict[str, str], value: Any = _NO_VALUE, error: int = 0, message: str = "") -> JSONResponse:
        try:
            client_tx = max(0, int(p.get("clienttransactionid", 0)))
        except ValueError:
            client_tx = 0
        body: dict[str, Any] = {"ClientTransactionID": client_tx, "ServerTransactionID": next(server_tx),
                                "ErrorNumber": error, "ErrorMessage": message}
        if value is not _NO_VALUE:
            body["Value"] = value
        return JSONResponse(body)

    def is_safe(request: Request) -> bool:
        monitor.polled = {"t": time.time(), "client": request.client.host if request.client else None}
        return device["connected"] and monitor.safe

    @app.get("/management/apiversions")
    async def api_versions(request: Request) -> Response:
        return reply(await params(request), [1])

    @app.get("/management/v1/description")
    async def description(request: Request) -> Response:
        return reply(await params(request), {"ServerName": "AstroDashboard", "Manufacturer": "AstroDashboard",
                                              "ManufacturerVersion": __version__, "Location": ""})

    @app.get("/management/v1/configureddevices")
    async def configured(request: Request) -> Response:
        return reply(await params(request), [{"DeviceName": DEVICE_NAME, "DeviceType": "SafetyMonitor", "DeviceNumber": 0,
                                              "UniqueID": UNIQUE_ID}])

    @app.get("/setup", response_class=HTMLResponse)
    @app.get("/setup/v1/safetymonitor/{number}/setup", response_class=HTMLResponse)
    async def setup() -> str:
        return (f"<!doctype html><meta charset=utf-8><title>{DEVICE_NAME}</title><body style='font-family:sans-serif'>"
                f"<h1>{DEVICE_NAME}</h1><p>Its settings are in AstroDashboard: Settings → Live · Ingest → Sky monitor.</p>")

    @app.api_route("/api/v1/safetymonitor/{number}/{member}", methods=["GET", "PUT"])
    async def member(number: str, member: str, request: Request) -> Response:
        p = await params(request)
        if number != "0":
            return PlainTextResponse(f"There is no safety monitor number {number}", status_code=400)
        name = member.lower()
        if request.method == "GET":
            getters: dict[str, Any] = {
                "issafe": lambda: is_safe(request),
                "connected": lambda: device["connected"],
                "connecting": lambda: False,
                "devicestate": lambda: [{"Name": "IsSafe", "Value": is_safe(request)},
                                        {"Name": "TimeStamp", "Value": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}],
                "name": lambda: DEVICE_NAME,
                "description": lambda: "Unsafe while PHD2's guide star is lost or dimmed by cloud, as AstroDashboard sees it.",
                "driverinfo": lambda: f"{DEVICE_NAME}: an ASCOM Alpaca SafetyMonitor served by the AstroDashboard engine.",
                "driverversion": lambda: __version__,
                "interfaceversion": lambda: INTERFACE_VERSION,
                "supportedactions": lambda: [],
            }
            if name not in getters:
                return PlainTextResponse(f"{member} cannot be read", status_code=400)
            return reply(p, getters[name]())
        if name == "connected":
            value = p.get("connected", "").strip().lower()
            if value not in ("true", "false"):
                return PlainTextResponse("Connected must be true or false", status_code=400)
            device["connected"] = value == "true"
            return reply(p)
        if name in ("connect", "disconnect"):
            device["connected"] = name == "connect"
            return reply(p)
        if name in ("action", "commandblind", "commandbool", "commandstring"):
            return reply(p, error=NOT_IMPLEMENTED, message=f"{member} is not implemented")
        return PlainTextResponse(f"{member} cannot be written", status_code=400)

    return app


class _Discovery(asyncio.DatagramProtocol):
    """Answers Alpaca discovery broadcasts with the device's HTTP port."""

    def __init__(self, port: int) -> None:
        self.answer = json.dumps({"AlpacaPort": port}).encode()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # noqa: ANN001 — asyncio's signature
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        if data.startswith(b"alpacadiscovery1") and self.transport is not None:
            self.transport.sendto(self.answer, addr)


class AlpacaServer:
    """Runs the app (and discovery) in a daemon thread; `monitor.server` says how that went, for the UI."""

    def __init__(self, monitor: SkyMonitor, host: str, port: int, discovery: bool = True) -> None:
        self.monitor, self.host, self.port, self.discovery = monitor, host, port, discovery
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.monitor.server = {"running": False, "port": self.port, "error": None, "discovery": False}
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), name="alpaca", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.monitor.server = {**self.monitor.server, "running": False}

    async def _serve(self) -> None:
        info = self.monitor.server
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((self.host, self.port))
        except OSError as exc:
            info["error"] = f"Could not listen on port {self.port}: {exc.strerror or exc}"
            log.error("sky monitor (Alpaca): %s", info["error"])
            return
        discovery = None
        if self.discovery:
            try:
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # other Alpaca servers on this PC share it
                udp.bind(("", DISCOVERY_PORT))
                discovery, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: _Discovery(self.port), sock=udp)
                info["discovery"] = True
            except OSError as exc:
                log.warning("sky monitor (Alpaca): no discovery on UDP %d (%s); NINA needs the address typed in", DISCOVERY_PORT, exc)
        config = uvicorn.Config(create_app(self.monitor), log_level="warning", access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        info["running"] = True
        log.info("sky monitor (Alpaca): %s:%d%s", self.host, self.port, ", discovery on" if discovery else "")
        try:
            await self._server.serve(sockets=[sock])
        finally:
            info["running"] = False
            if discovery is not None:
                discovery.close()
