"""
GET /forest/status — proxy to the Forest Status API on port 7438.

Forest runs a separate read-only FastAPI on 127.0.0.1:7438.
This endpoint surfaces active incidents in buddy's UI without coupling
the two services beyond a single HTTP call.

If forest is offline the endpoint returns a degraded (not an error) response
so buddy's UI can show "forest offline" gracefully.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/forest", tags=["forest"])

_FOREST_URL = "http://127.0.0.1:7438/forest/status"
_TIMEOUT = 2.0  # seconds — forest is local, should respond fast


@router.get("/status")
async def forest_status():
    """Proxy to Forest Status API. Returns degraded response if forest is offline or test mode is on."""
    # Avoid circular import — import lazily
    from buddy.api.admin import is_test_mode
    if is_test_mode():
        return JSONResponse(
            status_code=200,
            content={
                "status": "paused",
                "total_logged": 0,
                "active_incidents": [],
                "severity_breakdown": {},
                "chain_length": 0,
                "improvements_logged": 0,
                "message": "Forest monitoring paused (test mode active)",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_FOREST_URL)
            resp.raise_for_status()
            return JSONResponse(content=resp.json())
    except (httpx.ConnectError, httpx.TimeoutException):
        return JSONResponse(
            status_code=200,  # 200 — "offline" is a valid state, not a buddy error
            content={
                "status": "offline",
                "total_logged": 0,
                "active_incidents": [],
                "severity_breakdown": {},
                "chain_length": 0,
                "improvements_logged": 0,
                "message": "Forest swarm not running — start with: cd ~/forest-blue-team-guardian && scripts/forest-api-start.sh",
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": str(e), "active_incidents": []},
        )
