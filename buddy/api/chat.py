"""
POST /chat        — send a message, get a response
POST /chat/stream — same but streams tokens via SSE
GET  /sessions    — list session IDs
GET  /history/:id — conversation history
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from buddy.llm.prompts import build_chat_prompt
from buddy.llm.router import route, local_chat_stream, opus_chat, grade_response_score, RouteResult, _GRADE_EXECUTOR, _should_escalate_on_keywords
from buddy.memory.store import append_message, get_history, upsert_fact, list_sessions
from buddy.memory.vectors import search_memory, upsert_memory
from buddy.tools.filesystem import read_file
from buddy.tools.shell import requires_confirmation

router = APIRouter(prefix="/chat", tags=["chat"])

_REMEMBER_RE  = re.compile(r"^REMEMBER:\s*(\S+?)=(.+)$", re.MULTILINE)
_READ_FILE_RE = re.compile(r"^READ_FILE:\s*(.+)$", re.MULTILINE)
_SHELL_RE     = re.compile(r"^SHELL:\s*(.+)$", re.MULTILINE)
_SEARCH_RE    = re.compile(r"^SEARCH:\s*(.+)$", re.MULTILINE)


# ── Request / Response models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""
    force_frontier: bool = False


class RubricScoreOut(BaseModel):
    name: str
    score: float
    weight: float
    weighted: float


class GradeOut(BaseModel):
    composite_score: float
    passed: bool
    rubrics: list[RubricScoreOut]
    thinking_trace: str   # empty string when thinking not available
    escalated: bool       # True when local was tried but fell below threshold


class ChatResponse(BaseModel):
    session_id: str
    response: str
    pending_confirmation: dict | None = None
    model_used: str
    grade: GradeOut | None = None    # null when grading unavailable


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_tool_calls(response: str) -> dict:
    remember   = {m.group(1): m.group(2).strip() for m in _REMEMBER_RE.finditer(response)}
    read_files = [m.group(1).strip() for m in _READ_FILE_RE.finditer(response)]
    shells     = [m.group(1).strip() for m in _SHELL_RE.finditer(response)]
    return {"remember": remember, "read_files": read_files, "shells": shells}


def _clean_response(text: str) -> str:
    text = _REMEMBER_RE.sub("", text)
    text = _READ_FILE_RE.sub("", text)
    text = _SHELL_RE.sub("", text)
    text = _SEARCH_RE.sub("", text)
    return text.strip()


def _grade_out(result: RouteResult) -> GradeOut | None:
    """Convert RouteResult.grade → GradeOut for the API response."""
    if result.grade is None:
        return None
    g = result.grade
    return GradeOut(
        composite_score=g.composite_score,
        passed=g.passed,
        rubrics=[
            RubricScoreOut(name=r.name, score=r.score,
                           weight=r.weight, weighted=r.weighted)
            for r in g.rubrics
        ],
        thinking_trace=g.thinking_trace,
        escalated=result.escalated,
    )


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    history = get_history(session_id)
    loop = asyncio.get_event_loop()
    # Skip vector search for trivial queries — saves ~500ms and avoids noise
    if len(req.message) >= 20:
        mem_ctx = await loop.run_in_executor(_GRADE_EXECUTOR, search_memory, req.message, 3)
    else:
        mem_ctx = []
    messages = build_chat_prompt(history, req.message, mem_ctx)

    try:
        result: RouteResult = await route(
            messages, session_id=session_id, force_frontier=req.force_frontier
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    raw_response = result.response

    # ── Tool directives ────────────────────────────────────────────────────────
    directives = _extract_tool_calls(raw_response)

    for k, v in directives["remember"].items():
        upsert_fact(k, v, source="assistant_inferred")

    tool_context = ""
    for path in directives["read_files"][:2]:
        try:
            tool_context += f"\n\n[FILE: {path}]\n{read_file(path)[:2000]}"
        except Exception as e:
            tool_context += f"\n\n[FILE ERROR: {path}] {e}"

    if tool_context:
        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user",
                         "content": f"File contents:{tool_context}\n\nContinue."})
        try:
            result = await route(messages, session_id=session_id,
                                 force_frontier=req.force_frontier)
            raw_response = result.response
        except Exception:
            pass

    # ── Shell gate ─────────────────────────────────────────────────────────────
    pending_confirmation = None
    if directives["shells"]:
        try:
            pending_confirmation = requires_confirmation(directives["shells"][0])
        except Exception as e:
            raw_response += f"\n\n[Shell blocked: {e}]"

    clean = _clean_response(raw_response)

    # ── Persist ────────────────────────────────────────────────────────────────
    append_message(session_id, "user", req.message)
    append_message(session_id, "assistant", clean, model=result.model_used)

    # Memory filter — only embed significant exchanges (cus-core score ≥ 70)
    _trivial = len(req.message) < 20 or len(clean) < 50
    if not _trivial:
        mem_score = (result.grade.composite_score
                     if result.grade else await grade_response_score(clean, req.message))
        if mem_score >= 70.0:
            upsert_memory(f"User: {req.message}\nAssistant: {clean[:300]}")

    return ChatResponse(
        session_id=session_id,
        response=clean,
        pending_confirmation=pending_confirmation,
        model_used=result.model_used,
        grade=_grade_out(result),
    )


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    """
    SSE streaming endpoint — yields tokens as they arrive.

    Event types:
      data: {"token": "..."}
          Incremental token (or full Opus response as one chunk).
      data: {"done": true, "session_id": "...", "model": "...",
             "escalated": false, "grade": {...}|null,
             "pending_confirmation": {...}|null}
          Stream complete — grade panel data and shell-gate info included.
      data: {"error": "..."}
          Unrecoverable failure.
    """
    session_id = req.session_id or str(uuid.uuid4())

    history = get_history(session_id)
    loop = asyncio.get_event_loop()
    # Skip vector search for trivial queries — saves ~500ms
    if len(req.message) >= 20:
        mem_ctx = await loop.run_in_executor(_GRADE_EXECUTOR, search_memory, req.message, 3)
    else:
        mem_ctx = []
    messages = build_chat_prompt(history, req.message, mem_ctx)

    from buddy.config import settings as _cfg
    import os as _os
    api_key = _cfg.anthropic_api_key or _os.environ.get("ANTHROPIC_API_KEY", "")

    # Routing decision (mirrors router.route() pre-checks)
    use_frontier = (
        (req.force_frontier and api_key)
        or (api_key and _should_escalate_on_keywords(req.message))
    )
    # keyword escalation = escalated flag; manual 🌐 = not escalated
    pre_escalated: bool = use_frontier and not req.force_frontier

    async def generate():
        full_text = ""
        model_used = ""
        grade = None
        final_escalated = pre_escalated

        try:
            if use_frontier:
                # Opus: non-streaming SDK call, delivered as a single token chunk
                result = await opus_chat(messages, session_id=session_id)
                result.escalated = pre_escalated
                full_text = result.response
                model_used = result.model_used
                grade = result.grade
                yield f"data: {json.dumps({'token': full_text})}\n\n"

            else:
                # Local model: stream tokens as they arrive
                model_used = _cfg.local_model
                async for token in local_chat_stream(messages):
                    full_text += token
                    yield f"data: {json.dumps({'token': token})}\n\n"

                # Grade post-stream — tokens already delivered, no client wait
                grade = await _local_grade_async(full_text, context=req.message, timeout=45.0)

            # ── Tool directives ────────────────────────────────────────────────
            directives = _extract_tool_calls(full_text)

            # REMEMBER: key=value → persist as user facts
            for k, v in directives["remember"].items():
                upsert_fact(k, v, source="assistant_inferred")

            # READ_FILE: fetch file contents, do a follow-up LLM call
            tool_context = ""
            for path in directives["read_files"][:2]:
                try:
                    tool_context += f"\n\n[FILE: {path}]\n{read_file(path)[:2000]}"
                except Exception as e:
                    tool_context += f"\n\n[FILE ERROR: {path}] {e}"

            if tool_context:
                follow_msgs = list(messages)
                follow_msgs.append({"role": "assistant", "content": full_text})
                follow_msgs.append({"role": "user",
                                    "content": f"File contents:{tool_context}\n\nContinue."})
                try:
                    if use_frontier:
                        follow = await opus_chat(follow_msgs, session_id=session_id)
                        follow_text = follow.response
                        yield f"data: {json.dumps({'token': chr(10) + chr(10) + follow_text})}\n\n"
                    else:
                        follow_text = ""
                        async for token in local_chat_stream(follow_msgs):
                            follow_text += token
                            yield f"data: {json.dumps({'token': token})}\n\n"
                    full_text = full_text + "\n\n" + follow_text
                except Exception:
                    pass

            # SHELL: queue pending confirmation gate
            pending_confirmation = None
            if directives["shells"]:
                try:
                    pending_confirmation = requires_confirmation(directives["shells"][0])
                except Exception:
                    pass

            clean = _clean_response(full_text)

            # ── Persist ────────────────────────────────────────────────────────
            append_message(session_id, "user", req.message)
            append_message(session_id, "assistant", clean, model=model_used)

            _trivial = len(req.message) < 20 or len(clean) < 50
            if not _trivial:
                mem_score = (grade.composite_score
                             if grade else await grade_response_score(clean, req.message))
                if mem_score >= 70.0:
                    upsert_memory(f"User: {req.message}\nAssistant: {clean[:300]}")

            # ── Build done payload ─────────────────────────────────────────────
            grade_payload = None
            if grade:
                grade_payload = {
                    "composite_score": grade.composite_score,
                    "passed": grade.passed,
                    "rubrics": [
                        {"name": r.name, "score": r.score,
                         "weight": r.weight, "weighted": r.weighted}
                        for r in grade.rubrics
                    ],
                    "thinking_trace": grade.thinking_trace,
                    "escalated": final_escalated,
                }

            done_payload: dict = {
                "done": True,
                "session_id": session_id,
                "model": model_used,
                "escalated": final_escalated,
                "grade": grade_payload,
            }
            if pending_confirmation:
                done_payload["pending_confirmation"] = pending_confirmation

            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
async def sessions():
    return {"sessions": list_sessions()}


@router.get("/history/{session_id}")
async def history(session_id: str, limit: int = 40):
    return {"messages": get_history(session_id, limit=limit)}
