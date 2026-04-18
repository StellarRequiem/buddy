"""
Local-first LLM router with cus-core grading.

Routing logic:
  1. Try local Qwen2.5:14b
  2. If local unavailable → fall back to phi4-mini
  3. Escalate to Claude Haiku only when:
     - caller explicitly requests it (force_frontier=True)
     - API key is available

Every frontier call is graded by cus-core and logged to SQLite.
"""
from __future__ import annotations

import os
from typing import AsyncGenerator

import httpx

from buddy.config import settings as cfg
from buddy.memory.store import log_grade

# cus-core grading for frontier calls
from cus_core.models import Rubric, Stage, StageName, Task
from cus_core.grader import Grader, MockGrader

_RESPONSE_RUBRIC = [
    Rubric(name="relevance",   weight=0.40, scoring="numeric",
           question="How directly does the response address what the user asked? (0-100)"),
    Rubric(name="accuracy",    weight=0.35, scoring="numeric",
           question="How factually correct and well-reasoned is the response? (0-100)"),
    Rubric(name="conciseness", weight=0.15, scoring="numeric",
           question="Is the response appropriately concise without losing clarity? (0-100)"),
    Rubric(name="safety",      weight=0.10, scoring="binary",
           question="Does the response avoid harmful, deceptive, or inappropriate content?"),
]

_RESPONSE_STAGE = Stage(
    name=StageName.ASSESS,
    grader_model="ollama:phi4-mini",   # grade locally, don't spend API on grading
    rubrics=_RESPONSE_RUBRIC,
)

_RESPONSE_TASK_TEMPLATE = Task(
    id="buddy_response",
    description="Generate a helpful, accurate, concise response to the user's message",
    expected_outcome="A direct, accurate, concise response that addresses the user's request",
    stages=[_RESPONSE_STAGE],
)

# Use phi4-mini as the local grader — small, fast, good enough for scoring
_GRADER = Grader(
    backends={"ollama": __import__("cus_core.grader", fromlist=["OllamaGrader"]).OllamaGrader()},
    pass_threshold=65.0,
)


async def local_chat(messages: list[dict], model: str | None = None) -> str:
    """Call Ollama chat API. Returns full response text."""
    target_model = model or cfg.local_model
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": target_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.7},
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


async def local_chat_stream(messages: list[dict], model: str | None = None) -> AsyncGenerator[str, None]:
    """Stream tokens from Ollama."""
    target_model = model or cfg.local_model
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": target_model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.7},
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                import json
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break


async def frontier_chat(messages: list[dict], session_id: str = "") -> str:
    """Call Claude Haiku. Returns full response. Requires ANTHROPIC_API_KEY."""
    api_key = cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY — cannot escalate to frontier")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    # Strip system message — Anthropic takes it separately
    system_msg = ""
    chat_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            chat_messages.append({"role": m["role"], "content": m["content"]})

    response = client.messages.create(
        model=cfg.frontier_model,
        max_tokens=1024,
        system=[{"type": "text", "text": system_msg,
                 "cache_control": {"type": "ephemeral"}}] if system_msg else [],
        messages=chat_messages,
    )
    result = response.content[0].text.strip()

    # Grade the frontier response and log it
    try:
        grade = _GRADER.grade(task=_RESPONSE_TASK_TEMPLATE, response=result)
        log_grade(
            session_id=session_id,
            call_type="frontier_chat",
            model=cfg.frontier_model,
            composite_score=grade.composite_score,
            passed=grade.passed,
            detail={"stage_scores": {
                sr.stage_name: round(sr.weighted_score, 2)
                for sr in grade.stage_results
            }},
        )
    except Exception:
        pass  # grading failure never blocks the response

    return result


async def grade_response_score(response: str, context: str = "") -> float:
    """
    Grade a response with phi4-mini via cus-core.
    Returns composite score 0–100. Used by chat.py to filter memory upserts.
    Defaults to 75.0 on grading failure so caller doesn't need to handle exceptions.
    """
    try:
        task = Task(
            id="buddy_memory_grade",
            description=f"User asked: {context[:120]}",
            expected_outcome="A direct, accurate, concise response that addresses the user's request",
            stages=[_RESPONSE_STAGE],
        )
        result = _GRADER.grade(task=task, response=response)
        return result.composite_score
    except Exception:
        return 75.0   # default pass — fail open so nothing is silently dropped


async def route(messages: list[dict], session_id: str = "",
                force_frontier: bool = False) -> str:
    """
    Main routing entry point.
    force_frontier=True → skip local, go straight to Claude Haiku.
    """
    if force_frontier:
        return await frontier_chat(messages, session_id=session_id)

    # Try primary local model
    try:
        return await local_chat(messages, model=cfg.local_model)
    except Exception:
        pass

    # Fallback to phi4-mini
    try:
        return await local_chat(messages, model=cfg.fallback_local_model)
    except Exception as e:
        raise RuntimeError(f"All local models failed: {e}") from e
