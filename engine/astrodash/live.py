"""Client of AstroController (PLAN §5): its state and images, proxied through the engine.

AstroController keeps owning NINA and PHD2. The engine only reads `/api/state`
and the two preview images. Its token comes from the ASTROCONTROLLER_TOKEN
environment variable and is added here, server side, so it never reaches the
browser and is never stored or returned.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

TOKEN_ENV = "ASTROCONTROLLER_TOKEN"


class AstroControllerClient:
    def __init__(self, url_getter, timeout_s: float = 4.0):
        self._url = url_getter
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self.last_state: dict[str, Any] | None = None
        self.last_ok: float | None = None
        self.last_error: str | None = None
        self.last_attempt: float = 0.0

    @property
    def url(self) -> str:
        return (self._url() or "http://127.0.0.1:3005").rstrip("/")

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(TOKEN_ENV)
        return {"X-Auth-Token": token} if token else {}

    def state(self) -> dict[str, Any]:
        self.last_attempt = time.time()
        try:
            r = httpx.get(f"{self.url}/api/state", headers=self._headers(), timeout=self.timeout_s)
            if r.status_code == 401:
                raise RuntimeError(f"AstroController refused the token (set {TOKEN_ENV} for the engine)")
            r.raise_for_status()
            data = r.json()
            with self._lock:
                self.last_state, self.last_ok, self.last_error = data, time.time(), None
            return {"online": True, "url": self.url, "state": data, "tokenSet": bool(os.environ.get(TOKEN_ENV))}
        except Exception as exc:  # noqa: BLE001 — offline is a normal state for this page
            with self._lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
            return {"online": False, "url": self.url, "error": self.last_error, "lastOk": self.last_ok,
                    "tokenSet": bool(os.environ.get(TOKEN_ENV))}

    def image(self, path: str) -> tuple[bytes, str] | None:
        try:
            r = httpx.get(f"{self.url}{path}", headers=self._headers(), timeout=self.timeout_s * 3)
            if r.status_code != 200:
                return None
            return r.content, r.headers.get("content-type", "image/png")
        except Exception:  # noqa: BLE001
            return None

    def refresh_if_stale(self, max_age_s: float = 30.0) -> None:
        with self._lock:
            # Offline: retry at most every max_age_s, so a dead link never slows the engine down.
            fresh = time.time() - self.last_attempt < max_age_s
        if not fresh:
            self.state()

    def sequence_running(self, max_age_s: float = 120.0) -> bool | None:
        """True/False from AstroController's sequence; None when it is offline or the state is stale."""
        with self._lock:
            st, ok = self.last_state, self.last_ok
        if not st or not ok or time.time() - ok > max_age_s:
            return None
        seq = st.get("sequence") or {}
        if not seq.get("available"):
            return None
        return any(s.get("status") == "RUNNING" for s in seq.get("steps") or [])
