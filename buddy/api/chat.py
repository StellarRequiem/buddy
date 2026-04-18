"""
POST /chat        — send a message, get a response
GET  /chat/stream — SSE streaming version
GET  /sessions    — list session IDs
"""
from __future__ import annotations

import re
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from buddy.llm.prompts import build_chat_prompt
from buddy.llm.router import route, local_chat_stream
from buddy.memory.store import append_message, get_history, upsert_fact, list_sessions
from buddy.memory.vectors import search_memory, upsert_memory
from buddy.tools.filesystem import read_file
from buddy.tools.shell import requires_confirmation

router = APIRouter(prefix="/chat", tags=["chat"])

_REMEMBER_RE  = re.compile(r"^REMEMBER:\s*(\S+?)=(.+)$", re.MULTILINE)
_READ_FILE_RE = re.compile(r"^READ_FILE:\s*(.+)$", re.MULTILINE)
_SHELL_RE     = re.compile(r"^SHELL:\s*(.+)$", re.MULTILINE)


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    force_frontier: bool = False


class ChatResponse(BaseModel):
    session_id: str
    response: str
    pending_confirmation: dict | None = None   # non-null if shell gate triggered
    model_used: str


def _extract_tool_calls(response: str) -> dict:
    """Pull structured directives out of the model's response text."""
    remember = {m.group(1): m.group(2).strip() for m in _REMEMBER_RE.finditer(response)}
    read_files = [m.group(1).strip() for m in _READ_FILE_RE.finditer(response)]
    shells = [m.group(1).strip() for m in _SHELL_RE.finditer(response)]
    return {"remember": remember, "read_files": read_files, "shells": shells}


def _clean_response(text: str) -> str:
    """Strip directive lines from the user-facing response."""
    text = _REMEMBER_RE.sub("", text)
    text = _READ_FILE_RE.sub("", text)
    text = _SHELL_RE.sub("", text)
    return text.strip()


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    # Retrieve context
    history = get_history(session_id)
    mem_ctx = search_memory(req.message, n_results=3)

    messages = build_chat_prompt(history, req.message, mem_ctx)

    model_used = "frontier" if req.force_frontier else "local"

    try:
        raw_response = await route(messages, session_id=session_id,
                                   force_frontier=req.force_frontier)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Process directives
    directives = _extract_tool_calls(raw_response)

    # Persist facts
    for k, v in directives["remember"].items():
        upsert_fact(k, v, source="assistant_inferred")

    # Handle file reads — inject results back as a follow-up
    tool_context = ""
    for path in directives["read_files"][:2]:   # cap at 2 reads per turn
        try:
            contents = read_file(path)
            tool_context += f"\n\n[FILE: {path}]\n{contents[:2000]}"
        except Exception as e:
            tool_context += f"\n\n[FILE ERROR: {path}] {e}"

    if tool_context:
        # Re-call model with file contents injected
        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user", "content": f"File contents:{tool_context}\n\nContinue your response."})
        try:
            raw_response = await route(messages, session_id=session_id,
                                       force_frontier=req.force_frontier)
        except Exception:
            pass

    # Handle shell gate
    pending_confirmation = None
    if directives["shells"]:
        try:
            pending_confirmation = requires_confirmation(directives["shells"][0])
        except Exception as e:
            raw_response += f"\n\n[Shell blocked: {e}]"

    clean = _clean_response(raw_response)

    # Persist to memory
    append_message(session_id, "user", req.message)
    append_message(session_id, "assistant", clean, model=model_used)
    upsert_memory(f"User: {req.message}\nAssistant: {clean[:300]}")

    return ChatResponse(
        session_id=session_id,
        response=clean,
        pending_confirmation=pending_confirmation,
        model_used=model_used,
    )


@router.get("/sessions")
async def sessions():
    return {"sessions": list_sessions()}


@router.get("/history/{session_id}")
async def history(session_id: str, limit: int = 40):
    return {"messages": get_history(session_id, limit=limit)}
