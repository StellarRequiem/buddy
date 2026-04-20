"""
POST /chat        — send a message, get a response
POST /chat/stream — same but streams tokens via SSE
GET  /sessions    — list session IDs
GET  /history/:id — conversation history

Streaming SSE event types:
  {"token": "..."}
      Incremental token.
  {"type": "tool_call",   "name": "...", "args": {...}, "iteration": N}
      Agent is calling a tool (show in activity panel).
  {"type": "tool_result", "name": "...", "preview": "...", "iteration": N}
      Tool returned a result.
  {"type": "shell_gate",  "payload": {...}}
      Shell command awaiting human approval.
  {"done": true, "session_id": "...", "model": "...",
   "escalated": false, "grade": {...}|null, "tools_called": N,
   "pending_confirmation": {...}|null}
      Stream complete.
  {"error": "..."}
      Unrecoverable failure.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from buddy.config import settings as cfg
from buddy.llm.prompts import build_chat_prompt
from buddy.llm.router import (
    route, local_chat_stream, opus_chat, grade_response_score,
    RouteResult, _GRADE_EXECUTOR, _should_escalate_on_keywords,
    _local_grade_async,
)
from buddy.llm.agent import run_agent_loop, run_agent_collect
from buddy.memory.store import append_message, get_history, upsert_fact, list_sessions
from buddy.memory.vectors import search_memory, upsert_memory

router = APIRouter(prefix="/chat", tags=["chat"])

# Prevent hammering Ollama: at most 3 concurrent agent runs.
# Local LLMs are single-threaded anyway — extra concurrency just queues tokens.
_AGENT_SEMAPHORE = asyncio.Semaphore(3)


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
    thinking_trace: str
    escalated: bool


class ChatResponse(BaseModel):
    session_id: str
    response: str
    pending_confirmation: dict | None = None
    model_used: str
    grade: GradeOut | None = None
    tools_called: int = 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _grade_out(result: RouteResult, escalated: bool = False) -> GradeOut | None:
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
        escalated=escalated,
    )


def _grade_dict(grade_out: GradeOut | None) -> dict | None:
    if grade_out is None:
        return None
    return {
        "composite_score": grade_out.composite_score,
        "passed": grade_out.passed,
        "rubrics": [
            {"name": r.name, "score": r.score,
             "weight": r.weight, "weighted": r.weighted}
            for r in grade_out.rubrics
        ],
        "thinking_trace": grade_out.thinking_trace,
        "escalated": grade_out.escalated,
    }


# ── Non-streaming endpoint ─────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    history = get_history(session_id, limit=cfg.chat_history_limit)
    loop = asyncio.get_event_loop()
    if len(req.message) >= 20:
        mem_ctx = await loop.run_in_executor(_GRADE_EXECUTOR, search_memory, req.message, 3)
    else:
        mem_ctx = []
    messages = build_chat_prompt(history, req.message, mem_ctx)

    import os as _os
    api_key = cfg.anthropic_api_key or _os.environ.get("ANTHROPIC_API_KEY", "")
    use_frontier = (
        (req.force_frontier and api_key)
        or (api_key and _should_escalate_on_keywords(req.message))
    )
    escalated = use_frontier and not req.force_frontier

    pending_confirmation: dict | None = None
    tools_called = 0
    grade_out: GradeOut | None = None

    try:
        async with _AGENT_SEMAPHORE:
            if use_frontier:
                result = await opus_chat(messages, session_id=session_id)
                result.escalated = escalated
                clean = result.response.strip()
                grade_out = _grade_out(result, escalated=escalated)
                model_used = result.model_used
            elif cfg.use_agent_loop:
                # ── Apex Predator: native tool-calling loop ─────────────────────
                full_text, tools_called, shell_gate = await run_agent_collect(
                    messages, model=cfg.conductor_model,
                    max_iterations=cfg.max_agent_iterations,
                )
                pending_confirmation = shell_gate
                clean = full_text.strip()
                model_used = cfg.conductor_model
                grade_detail = await _local_grade_async(clean, context=req.message, timeout=45.0)
                if grade_detail:
                    grade_out = GradeOut(
                        composite_score=grade_detail.composite_score,
                        passed=grade_detail.passed,
                        rubrics=[
                            RubricScoreOut(name=r.name, score=r.score,
                                           weight=r.weight, weighted=r.weighted)
                            for r in grade_detail.rubrics
                        ],
                        thinking_trace=grade_detail.thinking_trace,
                        escalated=False,
                    )
            else:
                # Legacy route
                result = await route(messages, session_id=session_id,
                                     force_frontier=req.force_frontier)
                clean = result.response.strip()
                grade_out = _grade_out(result, escalated=result.escalated)
                model_used = result.model_used
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    # ── Persist ────────────────────────────────────────────────────────────────
    append_message(session_id, "user", req.message)
    append_message(session_id, "assistant", clean, model=model_used)

    _trivial = len(req.message) < 20 or len(clean) < 50
    if not _trivial:
        mem_score = (grade_out.composite_score
                     if grade_out else await grade_response_score(clean, req.message))
        if mem_score >= 70.0:
            upsert_memory(f"User: {req.message}\nAssistant: {clean[:300]}")

    return ChatResponse(
        session_id=session_id,
        response=clean,
        pending_confirmation=pending_confirmation,
        model_used=model_used,
        grade=grade_out,
        tools_called=tools_called,
    )


# ── Streaming endpoint ─────────────────────────────────────────────────────────

@router.post("/stream")
async def chat_stream(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())

    history = get_history(session_id, limit=cfg.chat_history_limit)
    loop = asyncio.get_event_loop()
    if len(req.message) >= 20:
        mem_ctx = await loop.run_in_executor(_GRADE_EXECUTOR, search_memory, req.message, 3)
    else:
        mem_ctx = []
    messages = build_chat_prompt(history, req.message, mem_ctx)

    import os as _os
    api_key = cfg.anthropic_api_key or _os.environ.get("ANTHROPIC_API_KEY", "")
    use_frontier = (
        (req.force_frontier and api_key)
        or (api_key and _should_escalate_on_keywords(req.message))
    )
    pre_escalated: bool = use_frontier and not req.force_frontier

    async def generate():
        full_text = ""
        model_used = ""
        grade_out: GradeOut | None = None
        final_escalated = pre_escalated
        tools_called = 0
        pending_confirmation: dict | None = None

        # Acquire semaphore for the entire stream duration.
        # Using explicit acquire/release (not `async with`) because `async with`
        # spanning yield points in a generator has surprising semantics.
        await _AGENT_SEMAPHORE.acquire()
        try:
            if use_frontier:
                # Opus: non-streaming SDK call, single chunk
                result = await opus_chat(messages, session_id=session_id)
                result.escalated = pre_escalated
                full_text = result.response
                model_used = result.model_used
                grade_out = _grade_out(result, escalated=pre_escalated)
                yield f"data: {json.dumps({'token': full_text})}\n\n"

            elif cfg.use_agent_loop:
                # ── Apex Predator: stream agent events ────────────────────────
                model_used = cfg.conductor_model
                async for event in run_agent_loop(
                    messages,
                    model=cfg.conductor_model,
                    max_iterations=cfg.max_agent_iterations,
                ):
                    etype = event.get("type")

                    if etype == "token":
                        token = event["token"]
                        full_text += token
                        yield f"data: {json.dumps({'token': token})}\n\n"

                    elif etype in ("tool_call", "tool_result"):
                        yield f"data: {json.dumps(event)}\n\n"

                    elif etype == "shell_gate":
                        pending_confirmation = event["payload"]

                    elif etype == "agent_done":
                        tools_called = event.get("tools_called", 0)

                    elif etype == "error":
                        yield f"data: {json.dumps({'error': event['message']})}\n\n"
                        return

                # Grade post-stream
                grade_detail = await _local_grade_async(
                    full_text, context=req.message, timeout=45.0
                )
                if grade_detail:
                    grade_out = GradeOut(
                        composite_score=grade_detail.composite_score,
                        passed=grade_detail.passed,
                        rubrics=[
                            RubricScoreOut(name=r.name, score=r.score,
                                           weight=r.weight, weighted=r.weighted)
                            for r in grade_detail.rubrics
                        ],
                        thinking_trace=grade_detail.thinking_trace,
                        escalated=False,
                    )

            else:
                # Legacy streaming path
                model_used = cfg.local_model
                async for token in local_chat_stream(messages):
                    full_text += token
                    yield f"data: {json.dumps({'token': token})}\n\n"
                grade_detail = await _local_grade_async(
                    full_text, context=req.message, timeout=45.0
                )
                if grade_detail:
                    grade_out = GradeOut(
                        composite_score=grade_detail.composite_score,
                        passed=grade_detail.passed,
                        rubrics=[
                            RubricScoreOut(name=r.name, score=r.score,
                                           weight=r.weight, weighted=r.weighted)
                            for r in grade_detail.rubrics
                        ],
                        thinking_trace=grade_detail.thinking_trace,
                        escalated=False,
                    )

            clean = full_text.strip()

            # ── Persist ────────────────────────────────────────────────────────
            append_message(session_id, "user", req.message)
            append_message(session_id, "assistant", clean, model=model_used)

            _trivial = len(req.message) < 20 or len(clean) < 50
            if not _trivial:
                mem_score = (grade_out.composite_score
                             if grade_out else await grade_response_score(clean, req.message))
                if mem_score >= 70.0:
                    upsert_memory(f"User: {req.message}\nAssistant: {clean[:300]}")

            # ── Done payload ───────────────────────────────────────────────────
            done_payload: dict[str, Any] = {
                "done": True,
                "session_id": session_id,
                "model": model_used,
                "escalated": final_escalated,
                "tools_called": tools_called,
                "grade": _grade_dict(grade_out),
            }
            if pending_confirmation:
                done_payload["pending_confirmation"] = pending_confirmation

            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            _AGENT_SEMAPHORE.release()

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
