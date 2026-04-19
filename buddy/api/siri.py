"""
/siri endpoint — optimised for iOS Shortcuts + Siri.

Differences from /chat:
- Returns plain text (not JSON) for Speak Text action in Shortcuts
- Shorter responses (max_tokens hint in prompt)
- GET /siri/ping  — connectivity test, returns "buddy online"
- POST /siri/ask  — send message, get plain text back
- POST /siri/task — create a task, returns confirmation string
- GET  /siri/status — one-line system status
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from buddy.llm.prompts import build_chat_prompt
from buddy.llm.router import route
from buddy.memory.store import append_message, get_history, create_task

router = APIRouter(prefix="/siri", tags=["siri"])

_SIRI_SUFFIX = "\n\nKeep your reply under 3 sentences. Plain language, no markdown."


@router.get("/ping", response_class=PlainTextResponse)
async def ping():
    """Shortcuts connectivity test."""
    return "buddy online"


class SiriAsk(BaseModel):
    message: str
    session_id: str = "siri-default"


@router.post("/ask", response_class=PlainTextResponse)
async def ask(req: SiriAsk):
    """Ask buddy something via Siri. Returns plain text for Speak Text action."""
    history = get_history(req.session_id, limit=10)
    messages = build_chat_prompt(history, req.message + _SIRI_SUFFIX)

    try:
        result = await route(messages, session_id=req.session_id)
    except Exception as e:
        return f"Buddy error: {e}"

    # Strip any markdown — Siri reads plain text
    clean = result.response.replace("**", "").replace("*", "").replace("`", "").replace("#", "").strip()

    append_message(req.session_id, "user", req.message)
    append_message(req.session_id, "assistant", clean, model=result.model_used)

    return clean


class SiriTask(BaseModel):
    title: str


@router.post("/task", response_class=PlainTextResponse)
async def task(req: SiriTask):
    """Create a task from Siri. Returns confirmation string."""
    task_id = create_task(req.title)
    return f"Task added: {req.title}"


@router.get("/status", response_class=PlainTextResponse)
async def status():
    """One-line status for Siri readback."""
    from buddy.memory.store import list_tasks
    from buddy.memory.vectors import memory_count
    open_tasks = len(list_tasks(status="queued")) + len(list_tasks(status="running"))
    mem = memory_count()
    return f"Buddy online. {open_tasks} open tasks, {mem} memory chunks."
