"""
Buddy -- FastAPI application entry point.
Starts at http://localhost:7437 by default.

Run:
  cd ~/Projects/buddy
  uv run python -m buddy.main
  # or
  uv run uvicorn buddy.main:app --reload
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from buddy.config import settings
from buddy.memory.db import init_db
from buddy.api.chat import router as chat_router
from buddy.api.tasks import router as tasks_router
from buddy.api.memory import router as memory_router
from buddy.api.siri import router as siri_router
from buddy.api.forest import router as forest_router
from buddy.api.demo import router as demo_router
from buddy.api.admin import router as admin_router
from buddy.api.alerts import router as alerts_router, start_alert_poller
from buddy.tools.shell import execute as shell_execute, consume_pending_token


# ── Lifespan: startup + graceful shutdown ──────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: run DB migrations, load plugins, start Forest alert poller
    init_db()
    from buddy.tools.plugin_loader import load_plugins
    load_plugins()
    poller_task = asyncio.create_task(start_alert_poller())
    yield
    # Shutdown: cancel poller, drain grading thread pool
    poller_task.cancel()
    from buddy.llm.router import _GRADE_EXECUTOR
    _GRADE_EXECUTOR.shutdown(wait=False)


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Buddy",
    description="Local-first personal assistant",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Static files and templates
_STATIC_DIR = Path(__file__).parent / "ui" / "static"
_TEMPLATES_DIR = Path(__file__).parent / "ui" / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
_INDEX_HTML = (_TEMPLATES_DIR / "index.html").read_text()

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(chat_router)
app.include_router(tasks_router)
app.include_router(memory_router)
app.include_router(siri_router)
app.include_router(forest_router)
app.include_router(demo_router)
app.include_router(admin_router)
app.include_router(alerts_router)


# ── UI ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


# ── Shell execute endpoint (called from frontend after human approval) ─────
class ShellExecRequest(BaseModel):
    command: str
    session_id: str = ""
    token: str = ""         # one-time CSRF token issued by requires_confirmation()


@app.post("/shell/execute")
async def shell_exec(req: ShellExecRequest):
    """
    Run a shell command.  Only reachable after the user clicks Approve in the UI.

    The *token* must match the one issued by requires_confirmation() for this
    exact command.  Tokens are single-use: replaying the request is rejected.
    """
    if not consume_pending_token(req.token, req.command):
        raise HTTPException(
            status_code=403,
            detail="Shell token invalid or expired. Re-submit the message to get a fresh token.",
        )
    output = shell_execute(req.command)
    return {"command": req.command, "output": output}


# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "vault": str(settings.vault_path)}


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "buddy.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
