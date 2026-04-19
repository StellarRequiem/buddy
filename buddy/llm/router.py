"""
LLM router — local-first with smart escalation to Opus 4.7.

Routing order:
  1. qwen2.5:14b (local, free)  — always tried first
  2. phi4-mini (local, free)    — if 14b unavailable
  3. Claude Opus 4.7 (API)      — escalates when:
       a) force_frontier=True (manual 🌐 toggle)
       b) local response scores below escalation_threshold (auto)
       c) message matches escalation_keywords in config

Grading:
  Local responses are graded by phi4-mini (free, fast) to decide escalation.
  Opus 4.7 responses are graded by Claude Haiku with extended thinking —
  the thinking trace is returned so the UI can show Haiku reasoning through
  each rubric criterion. This is the "Keep Thinking" demo moment.

Credit strategy:
  - phi4-mini grades every local response (~0.1s, no cost)
  - Opus 4.7 only fires when local quality is insufficient
  - Haiku grades Opus responses (~$0.25/1M tokens — cheap relative to Opus)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import AsyncGenerator

import httpx

from buddy.config import settings as cfg
from buddy.memory.store import log_grade

from cus_core.models import Rubric, Stage, StageName, Task
from cus_core.grader import Grader
try:
    from cus_core.grader import OllamaGrader
except ImportError:
    OllamaGrader = None  # type: ignore


# ── Data containers ────────────────────────────────────────────────────────────

@dataclass
class RubricScore:
    name: str
    score: float        # 0–100
    weight: float
    weighted: float     # score × weight

@dataclass
class GradeDetail:
    composite_score: float
    passed: bool
    rubrics: list[RubricScore] = field(default_factory=list)
    thinking_trace: str = ""     # Haiku's extended-thinking chain (Opus responses only)

@dataclass
class RouteResult:
    response: str
    model_used: str
    grade: GradeDetail | None = None
    escalated: bool = False      # True when local was tried but fell below threshold


# ── cus-core rubric ────────────────────────────────────────────────────────────

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
    grader_model="ollama:phi4-mini",
    rubrics=_RESPONSE_RUBRIC,
)

_RESPONSE_TASK_TEMPLATE = Task(
    id="buddy_response",
    description="Generate a helpful, accurate, concise response to the user's message",
    expected_outcome="A direct, accurate, concise response that addresses the user's request",
    stages=[_RESPONSE_STAGE],
)

_LOCAL_GRADER = Grader(
    backends={"ollama": OllamaGrader()} if OllamaGrader else {},
    pass_threshold=65.0,
)


# ── Local grading (phi4-mini, free) ───────────────────────────────────────────

def _should_escalate_on_keywords(message: str) -> bool:
    """Check if message contains any configured escalation keywords."""
    msg_lower = message.lower()
    return any(kw.lower() in msg_lower for kw in cfg.escalation_keywords)


def _local_grade(response: str, context: str = "") -> GradeDetail | None:
    """
    Grade a local response with phi4-mini via cus-core.
    Returns None on failure (treats as pass — avoids spurious escalation).
    """
    if not OllamaGrader:
        return None
    try:
        task = Task(
            id="local_grade",
            description=f"User asked: {context[:120]}",
            expected_outcome="A direct, accurate, concise response",
            stages=[_RESPONSE_STAGE],
        )
        stage_key = _RESPONSE_STAGE.name.value   # e.g. "assess"
        result = _LOCAL_GRADER.grade(task=task, stage_outputs={stage_key: response})

        # Build per-rubric display scores.
        # cus-core binary rubrics return 0/1; normalize to 0/100 for display.
        raw_scores: dict[str, float] = {}
        if result.stage_results:
            raw_scores = dict(result.stage_results[0].scores or {})

        rubrics = []
        for r in _RESPONSE_RUBRIC:
            raw = float(raw_scores.get(r.name, 0))
            # Normalize binary scores (0 or 1) → (0 or 100)
            display_score = raw * 100 if r.scoring == "binary" and raw <= 1.0 else raw
            rubrics.append(RubricScore(
                name=r.name,
                score=round(display_score, 1),
                weight=r.weight,
                weighted=round(display_score * r.weight, 1),
            ))

        return GradeDetail(
            composite_score=round(result.composite_score, 1),
            passed=result.passed,
            rubrics=rubrics,
        )
    except Exception:
        return None


# ── Haiku grader with extended thinking (for Opus responses) ──────────────────

def _build_grader_prompt(response: str, user_message: str = "") -> str:
    rubric_lines = "\n".join(
        f"- {r.name} (weight {r.weight:.0%}): {r.question}"
        for r in _RESPONSE_RUBRIC
    )
    return f"""You are a rigorous evaluator scoring an AI assistant's response.

USER MESSAGE: {user_message or "(not provided)"}

ASSISTANT RESPONSE TO EVALUATE:
{response}

RUBRIC (score each 0-100, binary rubrics score either 0 or 100):
{rubric_lines}

Return ONLY valid JSON with this exact structure:
{{
  "relevance": <number 0-100>,
  "accuracy": <number 0-100>,
  "conciseness": <number 0-100>,
  "safety": <0 or 100>
}}

No explanation outside the JSON."""


def _parse_scores(text: str) -> dict[str, float]:
    text = text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
    start, end = text.find("{"), text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    return json.loads(text)


def _build_grade_detail(scores: dict[str, float],
                         thinking_trace: str = "") -> GradeDetail:
    rubric_scores: list[RubricScore] = []
    composite = 0.0
    for r in _RESPONSE_RUBRIC:
        raw = float(scores.get(r.name, 0))
        weighted = raw * r.weight
        composite += weighted
        rubric_scores.append(RubricScore(
            name=r.name, score=raw, weight=r.weight, weighted=round(weighted, 1),
        ))
    return GradeDetail(
        composite_score=round(composite, 1),
        passed=composite >= 65.0,
        rubrics=rubric_scores,
        thinking_trace=thinking_trace,
    )


async def _grade_with_thinking(response: str, user_message: str = "",
                                session_id: str = "") -> GradeDetail | None:
    """
    Grade an Opus 4.7 response using Haiku with extended thinking.
    The thinking trace captures Haiku's full reasoning before it scores.
    Falls back to Haiku without thinking if budget is 0 or if thinking
    is unavailable on the current API tier.
    """
    api_key = cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    prompt = _build_grader_prompt(response, user_message)
    thinking_trace = ""
    scores_text = ""

    try:
        if cfg.grader_thinking_budget > 0:
            grader_resp = client.messages.create(
                model=cfg.grader_model,
                max_tokens=256 + cfg.grader_thinking_budget,
                thinking={"type": "enabled",
                           "budget_tokens": cfg.grader_thinking_budget},
                messages=[{"role": "user", "content": prompt}],
            )
            for block in grader_resp.content:
                if block.type == "thinking":
                    thinking_trace = block.thinking
                elif block.type == "text":
                    scores_text = block.text
        else:
            raise ValueError("thinking disabled")
    except Exception:
        # Fallback: Haiku without extended thinking
        try:
            import anthropic as _anthropic
            _client = _anthropic.Anthropic(api_key=api_key)
            grader_resp = _client.messages.create(
                model=cfg.grader_model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            scores_text = grader_resp.content[0].text
        except Exception:
            return None

    if not scores_text:
        return None

    try:
        scores = _parse_scores(scores_text)
        grade = _build_grade_detail(scores, thinking_trace=thinking_trace)
        log_grade(
            session_id=session_id,
            call_type="opus_graded",
            model=cfg.opus_model,
            composite_score=grade.composite_score,
            passed=grade.passed,
            detail={
                "rubrics": {r.name: r.score for r in grade.rubrics},
                "thinking_chars": len(thinking_trace),
            },
        )
        return grade
    except Exception:
        return None


# ── Model call functions ───────────────────────────────────────────────────────

async def opus_chat(messages: list[dict], session_id: str = "") -> RouteResult:
    """Call Claude Opus 4.7. Returns RouteResult with graded response."""
    api_key = cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    system_msg = ""
    chat_messages: list[dict] = []
    user_message = ""
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            chat_messages.append({"role": m["role"], "content": m["content"]})
            if m["role"] == "user":
                user_message = m["content"]

    api_resp = client.messages.create(
        model=cfg.opus_model,
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": system_msg,
            "cache_control": {"type": "ephemeral"},
        }] if system_msg else [],
        messages=chat_messages,
    )
    result = api_resp.content[0].text.strip()
    grade = await _grade_with_thinking(result, user_message=user_message,
                                        session_id=session_id)
    return RouteResult(response=result, model_used=cfg.opus_model, grade=grade)


async def local_chat(messages: list[dict], model: str | None = None) -> str:
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


async def local_chat_stream(messages: list[dict],
                             model: str | None = None) -> AsyncGenerator[str, None]:
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
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break


# ── Memory filter helper ───────────────────────────────────────────────────────

async def grade_response_score(response: str, context: str = "") -> float:
    """Grade with phi4-mini for memory upsert filtering. Returns 75.0 on failure."""
    grade = _local_grade(response, context=context)
    return grade.composite_score if grade else 75.0


# ── Main entry point ───────────────────────────────────────────────────────────

async def route(messages: list[dict], session_id: str = "",
                force_frontier: bool = False) -> RouteResult:
    """
    Local-first routing with smart escalation.

    Always tries local models first to preserve API credits.
    Escalates to Opus 4.7 when:
      - force_frontier=True  (manual 🌐 toggle)
      - local score < escalation_confidence_threshold (auto)
      - message matches escalation_keywords (complexity signal)
    """
    api_key = cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    # Extract last user message for keyword check
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )

    # ── Manual escalation ──────────────────────────────────────────────────────
    if force_frontier and api_key:
        return await opus_chat(messages, session_id=session_id)

    # ── Try local first ────────────────────────────────────────────────────────
    local_text: str | None = None
    local_model_used = cfg.local_model

    try:
        local_text = await local_chat(messages, model=cfg.local_model)
    except Exception:
        try:
            local_text = await local_chat(messages, model=cfg.fallback_local_model)
            local_model_used = cfg.fallback_local_model
        except Exception:
            local_text = None

    if local_text is None:
        # All local models failed — escalate if possible, else raise
        if api_key:
            result = await opus_chat(messages, session_id=session_id)
            result.escalated = True
            return result
        raise RuntimeError("All local models failed and no API key available")

    # ── Grade local response ───────────────────────────────────────────────────
    local_grade = _local_grade(local_text, context=last_user)
    local_score = local_grade.composite_score if local_grade else 75.0

    # Escalation threshold: config value is 0.0–1.0 scale, score is 0–100
    threshold = cfg.escalation_confidence_threshold * 100

    keyword_escalate = _should_escalate_on_keywords(last_user)
    score_escalate = local_score < threshold

    if api_key and (keyword_escalate or score_escalate):
        result = await opus_chat(messages, session_id=session_id)
        result.escalated = True
        return result

    # ── Return local result ────────────────────────────────────────────────────
    return RouteResult(
        response=local_text,
        model_used=local_model_used,
        grade=local_grade,
        escalated=False,
    )
