"""N.I.N.A. "Advanced API" plugin: REST (truth) and websocket (change notifications).

Every REST answer is wrapped in `{"Response": …, "Error": "", "StatusCode": 200, "Success": true}`;
`_unwrap` returns the payload or raises. The websocket at `/v2/socket` pushes coarse events
(IMAGE-SAVE, SEQUENCE-STARTING, MOUNT-BEFORE-FLIP …); a reconnect means missed events, so the
runtime re-reads REST state rather than replaying anything. NINA emits no per-step event,
so the sequence tree is polled.

The plugin has no authentication and binds every interface: keep it on the LAN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

# Devices polled for the equipment panel. Anything not configured in the NINA profile answers
# with an error and is simply not shown.
DEVICES = ("camera", "mount", "focuser", "filterwheel", "rotator", "guider", "flatdevice",
           "weather", "safetymonitor", "dome", "switch")


class ClockOffset:
    """How far NINA's clock is ahead of ours (seconds), from the `Date` header of its HTTP answers.

    NINA's timestamps (ExposureEndTime …) are on the observatory PC's clock, which can be many seconds
    off this machine's. `Date` has 1-second resolution, but each answer bounds the offset:
    the server read its clock between our send (t0) and receive (t1), so offset ∈ [D − t1, D + 1 − t0).
    Intersecting the bounds of the last answers narrows it to well under a second.
    """

    def __init__(self, keep: int = 40) -> None:
        self._bounds: deque[tuple[float, float]] = deque(maxlen=keep)

    def add(self, date_header: str | None, t0: float, t1: float) -> None:
        if not date_header:
            return
        try:
            d = parsedate_to_datetime(date_header).timestamp()
        except (TypeError, ValueError):
            return
        self._bounds.append((d - t1, d + 1 - t0))

    @property
    def seconds(self) -> float:
        if not self._bounds:
            return 0.0
        lo = max(b[0] for b in self._bounds)
        hi = min(b[1] for b in self._bounds)
        if lo > hi:  # a clock was adjusted meanwhile: trust only the latest answer
            lo, hi = self._bounds[-1]
            self._bounds.clear()
            self._bounds.append((lo, hi))
        return (lo + hi) / 2


class NinaError(RuntimeError):
    pass


class NinaUnavailable(ConnectionError):
    pass


class NinaRest:
    def __init__(self, root: str, timeout: float = 8.0) -> None:
        """`root` is http://host:port (or the simulator's http://127.0.0.1:3007/fake-nina)."""
        root = root.rstrip("/")
        self.base = f"{root}/v2/api"
        self.ws_url = "ws" + root[4:] + "/v2/socket" if root.startswith("http") else f"ws://{root}/v2/socket"
        self._client = httpx.AsyncClient(base_url=self.base, timeout=timeout, headers={"Accept": "application/json"})
        self.clock = ClockOffset()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, timeout: float | None = None, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        t0 = time.time()
        try:
            resp = await self._client.get(path, params=clean, **({"timeout": timeout} if timeout else {}))
        except httpx.HTTPError as exc:
            raise NinaUnavailable(f"{path}: {type(exc).__name__}") from exc
        self.clock.add(resp.headers.get("date"), t0, time.time())
        try:
            body = resp.json()
        except ValueError as exc:
            raise NinaError(f"{path}: non-JSON answer ({resp.status_code})") from exc
        if isinstance(body, dict) and "Success" in body:
            if body.get("Success") is False:
                raise NinaError(f"{path}: {body.get('Error') or 'error'}")
            return body.get("Response")
        return body

    async def get_bytes(self, path: str, **params: Any) -> tuple[bytes, str]:
        clean = {k: v for k, v in params.items() if v is not None}
        try:
            resp = await self._client.get(path, params=clean, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NinaUnavailable(f"{path}: {type(exc).__name__}") from exc
        ctype = resp.headers.get("content-type", "image/jpeg")
        # NINA reports a failed render as HTTP 200 with a JSON envelope instead of an image.
        if "json" in ctype:
            try:
                err = resp.json().get("Error") or "error"
            except ValueError:
                err = "non-image answer"
            raise NinaError(f"{path}: {err}")
        return resp.content, ctype

    async def equipment(self) -> dict[str, Any]:
        """Every device at once (concurrently: a stalled focuser must not delay the camera)."""

        async def one(name: str) -> tuple[str, Any]:
            try:
                return name, await self.get(f"/equipment/{name}/info")
            except NinaError as exc:
                return name, {"Connected": False, "Error": str(exc)}

        results = await asyncio.gather(*(one(n) for n in DEVICES))
        return dict(results)

    async def image_history(self) -> list[dict]:
        rows = await self.get("/image-history", all="true")
        return rows if isinstance(rows, list) else []

    async def image_jpeg(self, index: int, scale: float = 0.5) -> tuple[bytes, str]:
        # No `debayer`: on a mono camera NINA answers debayer=true with "Unknown error".
        return await self.get_bytes(f"/image/{index}", stream="true", resize="true", scale=scale, quality=85)


async def listen(url: str, on_event: Callable[[str, dict], Awaitable[None]],
                 on_connect: Callable[[], Awaitable[None]]) -> None:
    """One websocket session; raises when it closes so the caller reconnects with backoff."""
    import websockets

    async with websockets.connect(url, ping_interval=20, open_timeout=10, max_size=8 * 1024 * 1024) as ws:
        log.info("NINA websocket connected")
        await on_connect()
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            payload = msg.get("Response", msg) if isinstance(msg, dict) else None
            if isinstance(payload, str):
                payload = {"Event": payload}
            if isinstance(payload, dict) and payload.get("Event"):
                try:
                    await on_event(str(payload["Event"]), payload)
                except Exception:  # noqa: BLE001 — a bad handler must not drop the socket
                    log.exception("NINA event handler failed")
    raise ConnectionError("NINA websocket closed")


# ---------------------------------------------------------------------- sequence

RUNNING = {"RUNNING"}
DONE = {"FINISHED", "SKIPPED"}
# Item fields worth showing for the running step (exposure items carry these in the Advanced API).
STEP_FIELDS = ("ExposureTime", "ExposureCount", "Iterations", "CompletedIterations", "Filter", "Gain", "Offset",
               "ImageType", "Binning")


@dataclass
class Step:
    id: str
    name: str
    status: str
    depth: int
    container: bool
    parent: str | None
    info: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "status": self.status, "depth": self.depth,
                "container": self.container, "parent": self.parent, "info": self.info}


CONTAINER_SUFFIX = re.compile(r"_Container$", re.IGNORECASE)


def _label(entry: Any) -> str:
    if isinstance(entry, dict):
        name = entry.get("Name")
        if isinstance(name, str) and name.strip():
            # NINA names containers "<name>_Container" (e.g. "IC 405_Container"): show "IC 405".
            return CONTAINER_SUFFIX.sub("", name.strip()) or name.strip()
        for key in ("Type", "SequenceItemType"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                # Type names arrive as "NINA.Sequencer.SequenceItem.Imaging.TakeExposure, NINA.Sequencer".
                return CONTAINER_SUFFIX.sub("", v.split(",")[0].rsplit(".", 1)[-1].strip())
    return "(unnamed)"


def _info(entry: dict) -> dict:
    out: dict[str, Any] = {}
    for key in STEP_FIELDS:
        v = entry.get(key)
        if isinstance(v, dict):  # Filter: {"Name": "Ha", …}; Binning: {"X": 1, "Y": 1}
            v = v.get("Name") or (f"{v.get('X')}x{v.get('Y')}" if "X" in v else None)
        if v not in (None, "", -1):
            out[key] = v
    # Loop conditions hold the progress of the container they belong to.
    for cond in entry.get("Conditions") or []:
        if isinstance(cond, dict) and "Iterations" in cond:
            out.setdefault("Iterations", cond.get("Iterations"))
            out.setdefault("CompletedIterations", cond.get("CompletedIterations"))
    return out


def state_targets(payload: Any) -> dict[str, str]:
    """Container name → NINA target name, from `/sequence/state` (only it carries the containers' Target).

    {"Name": "IC 405_Container", "Target": {"TargetName": "IC 405", …}} → {"IC 405_Container": "IC 405"}.
    TargetName is also what NINA writes into the frames' OBJECT header.
    """
    out: dict[str, str] = {}

    def walk(e: Any) -> None:
        if isinstance(e, dict):
            t = e.get("Target")
            name = t.get("TargetName") if isinstance(t, dict) else None
            if isinstance(name, str) and name.strip() and isinstance(e.get("Name"), str):
                out.setdefault(e["Name"], name.strip())
            for c in e.get("Items") or []:
                walk(c)
        elif isinstance(e, list):
            for c in e:
                walk(c)

    walk(payload)
    return out


def parse_sequence(payload: Any, targets: dict[str, str] | None = None) -> dict:
    """Flatten `/sequence/json` (nested containers of unbounded depth) into rows + a summary.

    `targets` (from `state_targets`) names the target of each target container.
    """
    steps: list[Step] = []
    counter = iter(range(1, 1_000_000))

    def walk(entry: dict, depth: int, parent: str | None) -> None:
        items = entry.get("Items")
        sid = f"s{next(counter)}"
        info = _info(entry)
        if targets and entry.get("Name") in targets:
            info["Target"] = targets[entry["Name"]]
        steps.append(Step(sid, _label(entry), str(entry.get("Status") or "").upper(), depth, isinstance(items, list), parent, info))
        if isinstance(items, list):
            for child in items:
                if isinstance(child, dict):
                    walk(child, depth + 1, sid)

    if isinstance(payload, dict):
        payload = [payload]
    for entry in payload if isinstance(payload, list) else []:
        if isinstance(entry, dict) and "GlobalTriggers" not in entry:
            walk(entry, 0, None)

    by_id = {s.id: s for s in steps}
    running = [s for s in steps if s.status in RUNNING]
    leaf = next((s for s in reversed(running) if not s.container), running[-1] if running else None)
    path: list[Step] = []
    s = leaf
    while s is not None:
        path.insert(0, s)
        s = by_id.get(s.parent) if s.parent else None
    leaves = [s for s in steps if not s.container]
    # The target: NINA's own TargetName of the running target container (from /sequence/state). Without it,
    # the running container just below the top-level area: "Targets" › "IC 405" › "ImagingNew" › …
    target = next((p.info["Target"] for p in reversed(path) if p.info.get("Target")), None)
    if target is None:
        target = path[1].name if len(path) >= 2 and path[1].container else (path[0].name if len(path) == 1 and path[0].container else None)
    status = "running" if running else ("finished" if leaves and all(s.status in DONE for s in leaves) else ("idle" if steps else "none"))
    return {
        "available": bool(steps),
        "status": status,
        "steps": [s.as_dict() for s in steps],
        "path": [p.name for p in path],
        "current": leaf.as_dict() if leaf else None,
        "target": target,
        "done": sum(1 for s in leaves if s.status in DONE),
        "total": len(leaves),
    }
