"""Phone notifications through the Planning ntfy settings (Settings → Notifications), for what needs you at night."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


async def notify(settings: dict, title: str, body: str, tags: str = "warning", priority: str = "high") -> bool:
    """Send it when notifications are on and a topic is set; True when ntfy took it."""
    n = settings.get("notify") or {}
    topic, server = n.get("ntfyTopic"), n.get("ntfyServer") or "https://ntfy.sh"
    if not n.get("enabled") or not topic:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{server.rstrip('/')}/{topic}", content=body.encode("utf-8"),
                             headers={"Title": title, "Tags": tags, "Priority": priority})
            r.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("notification %r failed: %s", title, exc)
        return False
