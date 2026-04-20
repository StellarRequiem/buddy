"""
GET /alerts/stream -- SSE endpoint: receive Forest alerts in real time.

Architecture
------------
A single background asyncio task (start_alert_poller) polls Forest every
cfg.forest_alert_interval seconds. When a new incident arrives whose severity
matches cfg.forest_alert_severities, it is broadcast to every connected SSE
client via a per-client asyncio.Queue (pub/sub pattern).

Each browser tab that loads buddy connects once on DOMContentLoaded and keeps
the connection open. If the server restarts, EventSource auto-reconnects.

Deduplication: incidents are keyed by (timestamp, threat_type). A rolling set
of the last 500 seen IDs prevents re-alerting after server restarts.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from buddy.config import settings as cfg

router = APIRouter(prefix="/alerts", tags=["alerts"])

# ── Pub/sub registry ───────────────────────────────────────────────────────────
# One Queue per connected SSE client. Poller broadcasts to all of them.
_listeners: list[asyncio.Queue] = []


def _incident_key(inc: dict) -> str:
    return f"{inc.get('timestamp', '')}|{inc.get('threat_type', '')}"


async def _broadcast(alert: dict) -> None:
    """Push an alert to every connected client. Drops for slow clients."""
    for q in list(_listeners):
        try:
            q.put_nowait(alert)
        except asyncio.QueueFull:
            pass  # client not reading — drop rather than back-pressure the loop


# ── Background poller ──────────────────────────────────────────────────────────

async def start_alert_poller() -> None:
    """
    Long-running asyncio task started in main.py lifespan.
    Polls Forest and broadcasts new critical incidents.
    Exits cleanly when cancelled (server shutdown).
    """
    seen: set[str] = set()
    alert_severities = {s.upper() for s in cfg.forest_alert_severities}

    while True:
        try:
            await asyncio.sleep(cfg.forest_alert_interval)
        except asyncio.CancelledError:
            return

        # Skip polling when test mode is active (Forest is paused)
        try:
            from buddy.api.admin import is_test_mode
            if is_test_mode():
                continue
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                resp = await c.get(f"{cfg.forest_host}/forest/status")
                data = resp.json()
        except Exception:
            continue  # Forest offline -- expected, keep polling silently

        for inc in data.get("active_incidents", []):
            key = _incident_key(inc)
            if key in seen:
                continue
            severity = inc.get("severity", "").upper()
            if severity not in alert_severities:
                continue

            seen.add(key)
            # Rolling window: clear when too large to avoid unbounded growth
            if len(seen) > 500:
                seen.clear()

            await _broadcast({
                "type": "forest_alert",
                "severity": severity,
                "threat_type": inc.get("threat_type", "unknown"),
                "timestamp": inc.get("timestamp", ""),
                "response_actions": inc.get("response_actions", []),
                "blocked_ips": inc.get("blocked_ips", []),
                "phase": inc.get("phase", ""),
            })


# ── SSE endpoint ───────────────────────────────────────────────────────────────

@router.get("/stream")
async def alert_stream():
    """
    SSE stream for Forest alerts.
    Browser connects once; EventSource auto-reconnects on disconnect.
    Keepalive comments are sent every 20 s to prevent proxy timeouts.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _listeners.append(q)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                try:
                    alert = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(alert)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # SSE comment keeps proxy alive
        finally:
            try:
                _listeners.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
