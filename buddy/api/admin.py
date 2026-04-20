"""
POST /admin/test-mode   — toggle test mode on/off
GET  /admin/status      — current mode + memory info
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from buddy.config import settings as cfg
from buddy.memory.store import upsert_fact, get_facts, get_tool_metrics

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Auth dependency ────────────────────────────────────────────────────────────

def _verify_admin_token(x_admin_token: str = Header(default="")) -> None:
    """
    Require X-Admin-Token header when ADMIN_TOKEN is set in config.
    If admin_token is empty (default), auth is skipped — safe for local installs.
    """
    expected = cfg.admin_token.strip()
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token")


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


@router.post("/test-mode", dependencies=[Depends(_verify_admin_token)])
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


@router.get("/config", dependencies=[Depends(_verify_admin_token)])
async def runtime_config():
    """
    Show the live (possibly runtime-mutated) configuration.
    Useful for confirming qwen3 auto-upgrade fired, checking disabled_tools, etc.
    Sensitive fields (anthropic_api_key, admin_token) are redacted.
    """
    return {
        "conductor_model":      cfg.conductor_model,
        "local_model":          cfg.local_model,
        "fallback_local_model": cfg.fallback_local_model,
        "opus_model":           cfg.opus_model,
        "use_agent_loop":       cfg.use_agent_loop,
        "max_agent_iterations": cfg.max_agent_iterations,
        "agent_timeout_seconds": cfg.agent_timeout_seconds,
        "disabled_tools":       cfg.disabled_tools,
        "chat_history_limit":   cfg.chat_history_limit,
        "escalation_confidence_threshold": cfg.escalation_confidence_threshold,
        "escalation_keywords":  cfg.escalation_keywords,
        "ollama_host":          cfg.ollama_host,
        "forest_host":          cfg.forest_host,
        "vault_path":           str(cfg.vault_path),
        "test_mode":            is_test_mode(),
        "anthropic_api_key":    "***" if cfg.anthropic_api_key else "(not set)",
        "brave_search_api_key": "***" if cfg.brave_search_api_key else "(not set)",
        "admin_token":          "***" if cfg.admin_token else "(not set)",
    }


@router.get("/tool-metrics", dependencies=[Depends(_verify_admin_token)])
async def tool_metrics():
    """
    Aggregate and recent tool-call metrics from the tool_calls table.
    Shows call counts, success rates, and avg latency per tool.
    """
    return get_tool_metrics()


# ── Runtime tool enable / disable ─────────────────────────────────────────────

class ToolToggleRequest(BaseModel):
    disabled: bool


@router.post("/tools/{tool_name}/toggle", dependencies=[Depends(_verify_admin_token)])
async def toggle_tool(tool_name: str, req: ToolToggleRequest):
    """
    Disable or re-enable a tool at runtime — no restart required.
    Changes are reflected immediately by execute_tool().
    NOTE: This mutates the in-process settings list; it does NOT persist across restarts.
    """
    from buddy.tools.tool_registry import _TOOL_MAP
    if tool_name not in _TOOL_MAP:
        raise HTTPException(status_code=404, detail=f"Unknown tool '{tool_name}'")

    current = list(cfg.disabled_tools)
    if req.disabled:
        if tool_name not in current:
            current.append(tool_name)
    else:
        current = [t for t in current if t != tool_name]
    cfg.disabled_tools = current

    return {
        "tool_name": tool_name,
        "disabled": tool_name in cfg.disabled_tools,
        "all_disabled": cfg.disabled_tools,
    }


# ── Tool test runner ───────────────────────────────────────────────────────────

class ToolTestRequest(BaseModel):
    tool_name: str
    args: dict = {}


@router.post("/tools/test", dependencies=[Depends(_verify_admin_token)])
async def test_tool_run(req: ToolTestRequest):
    """
    Execute a tool directly from the admin UI — useful for verifying a tool works
    after changing config or as a smoke-test before deployment.
    The tool is subject to the normal disabled_tools check.
    """
    import time
    from buddy.tools.tool_registry import execute_tool

    t0 = time.monotonic()
    try:
        result = await execute_tool(req.tool_name, req.args)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        ok = not (result.startswith("[") and "error" in result.lower())
        return {"ok": ok, "result": result, "elapsed_ms": elapsed_ms}
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "result": str(exc), "elapsed_ms": elapsed_ms}


@router.get("/status", dependencies=[Depends(_verify_admin_token)])
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
