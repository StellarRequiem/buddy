"""
Native tool-calling agentic loop — the Apex Predator core.

Architecture:
  1. Build messages with full tool schemas (TOOL_SCHEMAS → Ollama `tools` param)
  2. POST /api/chat  — model returns either text OR tool_calls[]
  3. For each tool call batch:
       a. Yield tool_call events (all upfront so UI shows pending state)
       b. Execute ALL tools in parallel via asyncio.gather()
       c. Yield tool_result events as they resolve
       d. Append tool result messages and loop
  4. Shell gate: if any result starts with [SHELL_GATE_PENDING], stop loop
     and surface the confirmation payload to the caller
  5. When model emits text (no more tool_calls): stream final content in chunks
  6. Hard stop at max_agent_iterations to prevent runaway loops

Falls back to legacy local_chat_stream() when cfg.use_agent_loop = False.

Tool result truncation: each result is capped at _MAX_TOOL_RESULT bytes
so long file reads / web responses don't bloat the context window.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import httpx

from buddy.config import settings as cfg
from buddy.tools.tool_registry import TOOL_SCHEMAS, execute_tool
from buddy.tools.shell import requires_confirmation

logger = logging.getLogger(__name__)

# Tool result injected into context is capped at this many chars
_MAX_TOOL_RESULT = 2_000
# Chunk size when delivering buffered final content as tokens
_TOKEN_CHUNK = 40
# Shell gate sentinel prefix
_SHELL_GATE_PREFIX = "[SHELL_GATE_PENDING]"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _truncate_result(text: str) -> str:
    if len(text) <= _MAX_TOOL_RESULT:
        return text
    return text[:_MAX_TOOL_RESULT] + f"\n…[truncated — {len(text)} chars total]"


def _preview(text: str, max_len: int = 120) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _is_shell_gate(result: str) -> bool:
    return result.startswith(_SHELL_GATE_PREFIX)


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


# ── Ollama calls ───────────────────────────────────────────────────────────────

async def _ollama_tool_call(messages: list[dict], model: str) -> dict:
    """
    POST /api/chat with tool schemas (non-streaming).
    Returns the full parsed JSON response.
    """
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": TOOL_SCHEMAS,
                "stream": False,
                "options": {"temperature": 0.4},
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _ollama_stream_final(messages: list[dict], model: str) -> AsyncGenerator[str, None]:
    """
    Stream the final text response (no tools parameter — pure generation).
    Used when we need real streaming after tool loop completes with no buffered content.
    """
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.7},
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("message", {}).get("content", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break


# ── Parallel tool executor ─────────────────────────────────────────────────────

async def _execute_tool_call(tc: dict) -> tuple[str, dict, str]:
    """Execute a single tool call. Returns (name, args, result_text)."""
    fn = tc.get("function", {})
    name: str = fn.get("name", "")
    args = _parse_args(fn.get("arguments", {}))
    try:
        result = await execute_tool(name, args)
    except Exception as exc:
        result = f"[Tool error: {exc}]"
    return name, args, result


# ── Main agentic loop ──────────────────────────────────────────────────────────

async def run_agent_loop(
    messages: list[dict],
    model: str | None = None,
    max_iterations: int | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Async generator yielding SSE-ready dicts:

      {"type": "tool_call",   "name": str, "args": dict, "iteration": int}
      {"type": "tool_result", "name": str, "preview": str, "full": str, "iteration": int}
      {"type": "token",       "token": str}
      {"type": "shell_gate",  "payload": dict}   — stops loop, caller shows confirmation
      {"type": "agent_done",  "iterations": int, "tools_called": int}
      {"type": "error",       "message": str}
    """
    target_model = model or cfg.conductor_model
    max_iter = max_iterations if max_iterations is not None else cfg.max_agent_iterations

    loop_messages = list(messages)
    iteration = 0
    tools_called = 0

    while iteration < max_iter:
        iteration += 1

        # ── Ask the model ──────────────────────────────────────────────────────
        try:
            resp = await _ollama_tool_call(loop_messages, model=target_model)
        except Exception as exc:
            yield {"type": "error", "message": f"Ollama request failed: {exc}"}
            return

        msg = resp.get("message", {})
        tool_calls: list[dict] = msg.get("tool_calls") or []
        content: str = (msg.get("content") or "").strip()

        # ── No tool calls → model is done ─────────────────────────────────────
        if not tool_calls:
            if content:
                # Deliver the buffered content in smooth ~40-char chunks
                for i in range(0, len(content), _TOKEN_CHUNK):
                    yield {"type": "token", "token": content[i:i + _TOKEN_CHUNK]}
            else:
                # Rare: empty response — make a fresh streaming call (no tools)
                async for token in _ollama_stream_final(loop_messages, model=target_model):
                    yield {"type": "token", "token": token}
            break

        # ── Emit all tool_call events upfront (UI shows pending state) ─────────
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = _parse_args(fn.get("arguments", {}))
            tools_called += 1
            yield {"type": "tool_call", "name": name, "args": args, "iteration": iteration}

        # Append the assistant message with tool_calls
        loop_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        # ── Execute ALL tools in parallel ──────────────────────────────────────
        results: list[tuple[str, dict, str]] = list(
            await asyncio.gather(
                *[_execute_tool_call(tc) for tc in tool_calls],
                return_exceptions=False,
            )
        )

        # ── Process results — check for shell gate ─────────────────────────────
        shell_gate_payload: dict | None = None
        for name, args, result_text in results:
            if _is_shell_gate(result_text):
                raw_command = result_text[len(_SHELL_GATE_PREFIX):].strip()
                try:
                    shell_gate_payload = requires_confirmation(raw_command)
                except Exception as exc:
                    shell_gate_payload = {"command": raw_command, "token": "",
                                          "error": str(exc)}
                # Still emit result event (with sanitised preview)
                yield {
                    "type": "tool_result",
                    "name": name,
                    "preview": f"⏸ awaiting approval: {raw_command[:80]}",
                    "full": raw_command,
                    "iteration": iteration,
                }
            else:
                truncated = _truncate_result(result_text)
                yield {
                    "type": "tool_result",
                    "name": name,
                    "preview": _preview(truncated),
                    "full": truncated,
                    "iteration": iteration,
                }
                loop_messages.append({"role": "tool", "content": truncated})

        if shell_gate_payload is not None:
            yield {"type": "shell_gate", "payload": shell_gate_payload}
            yield {"type": "agent_done", "iterations": iteration, "tools_called": tools_called}
            return

    else:
        # Hit max iterations — do one last streaming synthesis
        yield {"type": "token",
               "token": "\n\n[Max tool iterations reached — summarising]\n\n"}
        async for token in _ollama_stream_final(loop_messages, model=target_model):
            yield {"type": "token", "token": token}

    yield {"type": "agent_done", "iterations": iteration, "tools_called": tools_called}


# ── Non-streaming collect helper ───────────────────────────────────────────────

async def run_agent_collect(
    messages: list[dict],
    model: str | None = None,
    max_iterations: int | None = None,
) -> tuple[str, int, dict | None]:
    """
    Collect all tokens into a string.
    Returns (full_text, tools_called, shell_gate_payload | None).
    """
    tokens: list[str] = []
    tools_called = 0
    shell_gate: dict | None = None

    async for event in run_agent_loop(messages, model=model, max_iterations=max_iterations):
        etype = event.get("type")
        if etype == "token":
            tokens.append(event["token"])
        elif etype == "agent_done":
            tools_called = event["tools_called"]
        elif etype == "shell_gate":
            shell_gate = event["payload"]
        elif etype == "error":
            tokens.append(f"\n[Agent error: {event['message']}]")

    return "".join(tokens), tools_called, shell_gate
