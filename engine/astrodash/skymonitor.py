"""`uv run astrodash-skymonitor`: the sky monitor NINA connects to, kept alive apart from the engine.

NINA's sequence cannot image without its safety monitor, so the device (`observatory/alpaca.py`) does not live in
the engine. Three things keep it up, each enough on its own:

* this supervisor runs the server as a child process and starts it again when it exits, or when it stops answering
  `UNHEALTHY` health checks in a row (a hang): NINA rides out a short gap (it sends Connected=true again);
* the scheduled task (`scripts/install-skymonitor-task.ps1`) starts the supervisor at logon and every minute (a
  second supervisor finds the first one's mutex and leaves at once);
* the engine starts it (`ensure_running`) when nothing answers on the port, detached: stopping or killing the
  engine (ProjectDashboard kills the whole process tree) leaves it running.

The server answers NINA from the engine's last verdict, and says clear when the engine has been silent for
`alpaca.STALE_S`: a crashed engine costs cloud protection, never the night.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

from .config import load_config
from .observatory import alpaca

log = logging.getLogger("astrodash.skymonitor")

MUTEX = "Local\\AstroDashboardSkyMonitor-{port}"  # one supervisor per port
HEALTH_S = 5.0  # between health checks
HEALTH_TIMEOUT_S = 3.0
UNHEALTHY = 3  # failed checks in a row: kill and restart the server
GRACE_S = 20.0  # a new server's time to start answering
MAX_BACKOFF_S = 30.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def _python() -> str:
    """pythonw beside this interpreter when there is one: no console window."""
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.is_file() else exe)


def _local(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


def answers(host: str, port: int, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """Whether the sky monitor answers on host:port."""
    try:
        r = httpx.get(f"http://{_local(host)}:{port}/api/v1/safetymonitor/0/name", timeout=timeout)
        return r.json().get("Value") == alpaca.DEVICE_NAME
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


_mutex_handle = None


def single_instance(port: int) -> bool:
    """Hold the supervisor's mutex for the life of this process; False when another supervisor holds it."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX.format(port=port))
    if not handle:
        return True  # cannot tell: run, the port settles it
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _mutex_handle = handle
    return True


def _raise_priority() -> None:
    """Above normal on Windows: the engine's process pools (framing, diagnostics) must not crowd NINA's replies out."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x8000)
        except Exception:  # noqa: BLE001 — a nicety
            pass


class Supervisor:
    """Runs the server and starts it again; `start(restarts)` launches it, `healthy()` asks it."""

    def __init__(self, start: Callable[[int], subprocess.Popen], healthy: Callable[[], bool],
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.start, self.healthy, self.sleep, self.clock = start, healthy, sleep, clock
        self.restarts = 0

    def run_once(self) -> float:
        """Run one server until it exits or hangs; returns how long to wait before the next one."""
        child = self.start(self.restarts)
        started, fails = self.clock(), 0
        while child.poll() is None:
            self.sleep(HEALTH_S)
            if self.clock() - started < GRACE_S:
                continue
            fails = 0 if self.healthy() else fails + 1
            if fails >= UNHEALTHY:
                log.error("the server stopped answering: restarting it")
                child.kill()
                child.wait()
                break
        ran = self.clock() - started
        log.warning("the server exited (code %s) after %.0f s", child.returncode, ran)
        self.restarts += 1
        return 1.0 if ran > 60 else min(MAX_BACKOFF_S, 2.0 ** min(self.restarts, 5))

    def run(self) -> None:
        while True:
            self.sleep(self.run_once())


def ensure_running(host: str, port: int) -> bool:
    """Start the supervisor, detached from this process, when nothing answers on the port. True when started."""
    if answers(host, port):
        return False
    _spawn_detached([_python(), "-m", "astrodash.skymonitor", "--detach"])
    return True


def _spawn_detached(args: list[str]) -> None:
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "cwd": str(Path(__file__).resolve().parents[1]), "close_fds": True}
    if sys.platform == "win32":
        try:
            subprocess.Popen(args, creationflags=DETACHED | NO_WINDOW | BREAKAWAY, **kw)
            return
        except OSError:  # the job does not allow breaking away
            subprocess.Popen(args, creationflags=DETACHED | NO_WINDOW, **kw)
    else:
        subprocess.Popen(args, start_new_session=True, **kw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AstroDashboard sky monitor (ASCOM Alpaca SafetyMonitor for NINA)")
    parser.add_argument("--serve", action="store_true", help="run the server itself (the supervisor runs this)")
    parser.add_argument("--detach", action="store_true", help="start the supervisor detached, and return")
    parser.add_argument("--restarts", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help="Alpaca port (default [alpaca] port, 3008)")
    parser.add_argument("--host", help="bind address (default [alpaca] host, every interface)")
    parser.add_argument("--no-discovery", action="store_true", help="do not answer Alpaca discovery")
    parser.add_argument("--engine-url", help="the engine's verdict endpoint (default from [server] port)")
    parser.add_argument("--config", type=Path, help="astrodashboard.toml path")
    args = parser.parse_args(argv)

    # Under pythonw (the scheduled task, the engine's launch) there is no stdout/stderr: uvicorn's logging needs one.
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 — for the process's life
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    host = args.host or cfg.alpaca_host
    port = args.port or cfg.alpaca_port
    discovery = cfg.alpaca_discovery and not args.no_discovery
    engine_url = args.engine_url or f"http://{_local(cfg.host)}:{cfg.port}/api/live/sky/alpaca"
    passed = [a for a in (argv if argv is not None else sys.argv[1:]) if a not in ("--detach", "--serve")]

    if args.detach:  # the engine's grandchild: start the supervisor and go, so it has no living parent
        _spawn_detached([_python(), "-m", "astrodash.skymonitor", *passed])
        return 0
    if args.serve:
        _raise_priority()
        return alpaca.serve(host, port, discovery, engine_url, args.restarts)
    if not port:
        log.info("[alpaca] port is 0: the sky monitor is off")
        return 0
    if not single_instance(port):
        return 0  # the scheduled task's every-minute start, while a supervisor runs
    _raise_priority()

    def start(restarts: int) -> subprocess.Popen:
        cmd = [_python(), "-m", "astrodash.skymonitor", "--serve", "--restarts", str(restarts), *passed]
        return subprocess.Popen(cmd, cwd=str(Path(__file__).resolve().parents[1]), creationflags=NO_WINDOW)

    log.info("supervising the sky monitor on port %d", port)
    Supervisor(start, lambda: answers(host, port)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
