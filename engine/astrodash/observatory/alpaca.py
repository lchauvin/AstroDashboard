"""The sky monitor as an ASCOM Alpaca SafetyMonitor, for NINA (Equipment › Safety Monitor).

NINA's sequence cannot image without it (Loop While Safe), so it runs apart from the engine, in its own process
(`astrodash/skymonitor.py` supervises it), and never waits on anything to answer NINA:

* NINA's Alpaca client disconnects a device whose reply takes a few seconds ("Dynamic client timeout for method
  Connected") and reads a disconnected safety monitor as unsafe, for the rest of the night. Served from the engine's
  process, every reply waited on the engine's GIL: garbage collections and long C calls stalled it 0.3–0.7 s every
  ~20 s even while idle, and longer while frames were ingested (2026-11-18, 23:54).
* The verdict comes from the engine (`POST /api/live/sky/alpaca`, every second, `Bridge`), and NINA is answered
  from the last one. An engine that has not answered for `STALE_S` gives no evidence, and no evidence means clear:
  NINA keeps imaging, without cloud protection, until the engine is back. A crashed engine never costs the night.

Its own port (`[alpaca] port`, 3008), bound to every interface: NINA runs on the observatory PC. Only this device is
served there. UDP discovery (port 32227) lets NINA find it without typing an address.

The device only reads the verdict; nothing NINA sends changes it. `Connected` is per client (ClientID), as ASCOM
asks of Alpaca devices with several clients: another app connecting and disconnecting must not disconnect NINA.
IsSafe is false for a client that is not connected, as ASCOM asks.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import __version__

log = logging.getLogger(__name__)

DISCOVERY_PORT = 32227
DEVICE_NAME = "AstroDashboard sky monitor"
UNIQUE_ID = "3f6d2b8e-6c1a-4f2e-9a57-0d4c8e5b7a21"
INTERFACE_VERSION = 3  # ISafetyMonitorV3 (ASCOM Platform 7): Connect/Disconnect/Connecting/DeviceState too
NOT_IMPLEMENTED = 0x400
KEEP_ALIVE_S = 120  # longer than .NET's pooled-connection idle timeout: NINA closes idle connections, never us
SYNC_S = 1.0  # how often the verdict is fetched from the engine
SYNC_TIMEOUT_S = 5.0
STALE_S = 120.0  # an engine silent this long gives no evidence: clear
_NO_VALUE = object()


def create_app(monitor: Any, clients: set[str] | None = None,
               on_connection: Callable[[str, str, str | None, set[str]], None] | None = None) -> FastAPI:
    """The Alpaca app. `monitor` has `.safe` (read) and `.polled` (written at each IsSafe); `clients` is the set of
    connected ClientIDs (used in place: the caller may add to it); `on_connection(what, client_id, address, clients)`
    hears each connect and disconnect."""
    app = FastAPI(title=DEVICE_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    connected: set[str] = clients if clients is not None else set()
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

    def client_id(p: dict[str, str]) -> str:
        return p.get("clientid", "").strip() or "0"

    def is_connected(p: dict[str, str]) -> bool:
        """This client's connection; without a ClientID, whether anyone is connected."""
        return client_id(p) in connected if p.get("clientid", "").strip() else bool(connected)

    def connect(p: dict[str, str], request: Request, on: bool) -> None:
        cid = client_id(p)
        if (cid in connected) == on:
            return
        (connected.add if on else connected.discard)(cid)
        if on_connection is not None:
            on_connection("connected" if on else "disconnected", cid, request.client.host if request.client else None,
                          set(connected))

    def is_safe(p: dict[str, str], request: Request) -> bool:
        monitor.polled = {"t": time.time(), "client": request.client.host if request.client else None}
        return is_connected(p) and monitor.safe

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
                "issafe": lambda: is_safe(p, request),
                "connected": lambda: is_connected(p),
                "connecting": lambda: False,
                "devicestate": lambda: [{"Name": "IsSafe", "Value": is_safe(p, request)},
                                        {"Name": "TimeStamp", "Value": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}],
                "name": lambda: DEVICE_NAME,
                "description": lambda: "Unsafe while PHD2's guide star is lost or dimmed by cloud, as AstroDashboard sees it.",
                "driverinfo": lambda: f"{DEVICE_NAME}: an ASCOM Alpaca SafetyMonitor served by AstroDashboard.",
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
            connect(p, request, value == "true")
            return reply(p)
        if name in ("connect", "disconnect"):
            connect(p, request, name == "connect")
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


class Bridge:
    """The device's side of the engine: the last verdict, and what NINA did, for the engine's log."""

    def __init__(self, port: int, discovery: bool = False, restarts: int = 0) -> None:
        self.port, self.discovery = port, discovery
        self.verdict = True
        self.last_ok: float | None = None  # when the engine last answered
        self.polled: dict | None = None  # set by the app at each IsSafe
        self.clients: set[str] = set()
        self.adopted = False  # the engine's list of connected clients (kept over restarts) taken on
        self.stale = False
        self.events: list[dict] = []
        start = "started" if not restarts else f"restarted (restart {restarts})"
        self.event(f"the sky monitor process {start}, pid {os.getpid()}")

    @property
    def safe(self) -> bool:
        if self.last_ok is None or time.time() - self.last_ok > STALE_S:
            return True  # no evidence: clear
        return self.verdict

    def event(self, message: str, **extra: Any) -> None:
        self.events.append({"t": time.time(), "message": message, **extra})
        del self.events[:-100]  # an engine away all night: keep the last ones

    def on_connection(self, what: str, cid: str, address: str | None, clients: set[str]) -> None:
        self.adopted = True  # what NINA does now overrides what the engine kept
        self.event(f"{address or 'a client'} (ClientID {cid}) {what}", clients=sorted(clients))

    def report(self) -> dict:
        return {"port": self.port, "discovery": self.discovery, "pid": os.getpid(), "polled": self.polled,
                "events": list(self.events)}

    def answered(self, data: dict, sent: int, now: float | None = None) -> None:
        """The engine took the report (its first `sent` events) and gave its verdict."""
        now = now or time.time()
        del self.events[:sent]
        if self.stale:
            self.stale = False
            self.event(f"the engine answers again after {now - (self.last_ok or now):.0f} s")
        self.verdict, self.last_ok = bool(data.get("safe", True)), now
        if not self.adopted:
            self.adopted = True
            self.clients.update(str(c) for c in data.get("clients") or [])

    def failed(self, now: float | None = None) -> None:
        now = now or time.time()
        if not self.stale and self.last_ok is not None and now - self.last_ok > STALE_S:
            self.stale = True
            self.event(f"the engine has not answered for {STALE_S:.0f} s: saying clear until it does")
            log.warning("the engine has not answered for %.0f s: saying clear", STALE_S)


async def sync(bridge: Bridge, url: str) -> None:
    """Fetch the verdict from the engine every `SYNC_S`, for as long as the server runs."""
    import httpx

    async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_S) as client:
        while True:
            report = bridge.report()
            try:
                resp = await client.post(url, json=report)
                resp.raise_for_status()
                bridge.answered(resp.json(), len(report["events"]))
            except (httpx.HTTPError, ValueError):
                bridge.failed()
            await asyncio.sleep(SYNC_S)


def serve(host: str, port: int, discovery: bool, engine_url: str, restarts: int = 0) -> int:
    """Serve the device until killed. Returns an exit code when it cannot (2: the port is taken)."""
    import uvicorn

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, port))
    except OSError as exc:
        log.error("could not listen on port %d: %s", port, exc.strerror or exc)
        return 2
    bridge = Bridge(port, discovery, restarts)
    app = create_app(bridge, bridge.clients, bridge.on_connection)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off",
                                           timeout_keep_alive=KEEP_ALIVE_S))

    async def run() -> None:
        udp_endpoint = None
        if discovery:
            try:
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # other Alpaca servers on this PC share it
                udp.bind(("", DISCOVERY_PORT))
                udp_endpoint, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: _Discovery(port), sock=udp)
            except OSError as exc:
                bridge.discovery = False
                log.warning("no discovery on UDP %d (%s); NINA needs the address typed in", DISCOVERY_PORT, exc)
        syncing = asyncio.create_task(sync(bridge, engine_url))
        try:
            await server.serve(sockets=[sock])
        finally:
            syncing.cancel()
            if udp_endpoint is not None:
                udp_endpoint.close()

    asyncio.run(run())
    return 0
