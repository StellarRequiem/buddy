"""
POST /admin/test-mode   — toggle test mode on/off
GET  /admin/status      — current mode + memory info
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from buddy.config import settings as cfg
from buddy.memory.store import upsert_fact, get_facts

router = APIRouter(prefix="/admin", tags=["admin"])

# ── Test-mode state ────────────────────────────────────────────────────────────
# Persisted in SQLite so it survives server restarts.
# _test_mode is the in-memory cache; SQLite is the source of truth on startup.

def _load_test_mode_from_db() -> bool:
    """Read persisted test_mode value from the user_facts table."""
    try:
        return get_facts().get("_test_mode") == "1"
    except Exception:
        return False


_test_mode: bool = _load_test_mode_from_db()


def is_test_mode() -> bool:
    return _test_mode or cfg.test_mode


async def _unload_model(model: str) -> bool:
    """Tell Ollama to evict a model from VRAM/RAM immediately."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"{cfg.ollama_host}/api/generate",
                json={"model": model, "keep_alive": 0},
            )
        return True
    except Exception:
        return False


async def _load_model(model: str) -> bool:
    """Pre-load a model into RAM."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"{cfg.ollama_host}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": "5m"},
            )
        return True
    except Exception:
        return False


class TestModeRequest(BaseModel):
    enabled: bool


@router.post("/test-mode")
async def set_test_mode(req: TestModeRequest):
    global _test_mode
    _test_mode = req.enabled
    # Persist across server restarts
    upsert_fact("_test_mode", "1" if req.enabled else "0", source="system")

    freed = []
    loaded = []

    if req.enabled:
        # Unload the big model to free ~9GB of RAM
        if await _unload_model(cfg.local_model):
            freed.append(cfg.local_model)
        # Kick phi4-mini into memory so it's warm
        await _load_model("phi4-mini")
        loaded.append("phi4-mini")
        msg = f"🔬 Test mode ON — freed {', '.join(freed)} — {', '.join(loaded)} warmed"
    else:
        # Re-load the full model when leaving test mode
        await _load_model(cfg.local_model)
        loaded.append(cfg.local_model)
        msg = f"✅ Test mode OFF — {cfg.local_model} loading back into RAM"

    return {"test_mode": _test_mode, "message": msg, "freed": freed, "loaded": loaded}


@router.get("/status")
async def admin_status():
    # Ask Ollama which models are currently loaded in RAM
    loaded_models = []
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            resp = await c.get(f"{cfg.ollama_host}/api/ps")
            loaded_models = [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass

    return {
        "test_mode": is_test_mode(),
        "local_model": cfg.local_model,
        "ollama_loaded": loaded_models,
    }
