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
import time
from typing import Any, AsyncGenerator

import httpx

# Exception types that indicate Ollama is unreachable (not a model error)
_OLLAMA_CONNECT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)

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
# Max tool-role messages kept in loop_messages before pruning oldest
_MAX_TOOL_MESSAGES = 12


# ── Helpers ────────────────────────────────────────────────────────────────────

# qwen3 wraps its chain-of-thought in <think>…</think> before responding or
# calling tools.  We split that out at stream time so the UI can render it in a
# collapsible "Reasoning" block without polluting the assistant's final reply.
_THINK_OPEN  = "<think>"   # 7 chars
_THINK_CLOSE = "</think>"  # 9 chars


def _emit_think_chunk(
    buf: str,
    in_think: bool,
) -> tuple[str, bool, list[tuple[str, str]]]:
    """
    Process one accumulated buffer pass through the think-tag state machine.

    Returns:
        (remaining_buf, new_in_think, events)
        events: list of ("token"|"thinking_trace", text)
    """
    events: list[tuple[str, str]] = []

    while buf:
        if not in_think:
            idx = buf.find(_THINK_OPEN)
            if idx == -1:
                # No complete open-tag.  Keep last 6 chars in case the tag
                # boundary is split across network chunks.
                safe_end = max(0, len(buf) - 6)
                if safe_end:
                    events.append(("token", buf[:safe_end]))
                return buf[safe_end:], in_think, events
            # Yield content before the tag
            if idx:
                events.append(("token", buf[:idx]))
            buf = buf[idx + len(_THINK_OPEN):]
            in_think = True
        else:
            idx = buf.find(_THINK_CLOSE)
            if idx == -1:
                # No complete close-tag.  Keep last 8 chars.
                safe_end = max(0, len(buf) - 8)
                if safe_end:
                    events.append(("thinking_trace", buf[:safe_end]))
                return buf[safe_end:], in_think, events
            if idx:
                events.append(("thinking_trace", buf[:idx]))
            buf = buf[idx + len(_THINK_CLOSE):]
            in_think = False

    return "", in_think, events


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


def _prune_tool_messages(messages: list[dict]) -> list[dict]:
    """
    Keep context window manageable by pruning oldest tool-role messages
    when they accumulate beyond _MAX_TOOL_MESSAGES.
    System and user/assistant messages are never pruned.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= _MAX_TOOL_MESSAGES:
        return messages
    # Drop the oldest half of tool messages
    to_drop = set(tool_indices[:len(tool_indices) // 2])
    return [m for i, m in enumerate(messages) if i not in to_drop]


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
    Used for the non-streaming /chat endpoint (run_agent_collect).
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


async def _ollama_stream_with_tools(
    messages: list[dict], model: str
) -> AsyncGenerator[tuple[str, Any], None]:
    """
    Stream /api/chat WITH tools enabled.
    Yields (event_type, data) tuples:
      ("thinking", str)    — content tokens streamed before tool calls are decided
      ("tool_calls", list) — final tool calls from the done chunk
      ("text", str)        — final text token when no tool calls are made

    Why streaming instead of non-streaming here:
    - qwen3 emits <think> reasoning tokens before calling tools — this gives
      the user live visibility into the model's reasoning (highly valuable UX)
    - qwen2.5 typically emits no thinking tokens, so behaviour is equivalent
      to non-streaming but we keep one code path for both models
    """
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": TOOL_SCHEMAS,
                "stream": True,
                "options": {"temperature": 0.4},
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
                msg = chunk.get("message", {})
                content = msg.get("content", "")
                if content:
                    yield ("thinking", content)
                if chunk.get("done"):
                    tool_calls = msg.get("tool_calls") or []
                    if tool_calls:
                        yield ("tool_calls", tool_calls)
                    break


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

async def _execute_tool_call(tc: dict, session_id: str = "") -> tuple[str, dict, str]:
    """Execute a single tool call. Returns (name, args, result_text)."""
    from buddy.memory.store import log_tool_call
    fn = tc.get("function", {})
    name: str = fn.get("name", "")
    args = _parse_args(fn.get("arguments", {}))
    t0 = time.monotonic()
    success = True
    try:
        result = await execute_tool(name, args)
        success = not result.startswith("[") or not result.endswith("error]")
    except Exception as exc:
        result = f"[Tool error: {exc}]"
        success = False
    latency_ms = int((time.monotonic() - t0) * 1000)
    args_summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in args.items())
    try:
        log_tool_call(name, success, latency_ms, session_id=session_id,
                      args_summary=args_summary, result_preview=_preview(result, 200))
    except Exception as exc:
        logger.warning("log_tool_call failed (metrics best-effort): %s", exc)
    return name, args, result


# ── Main agentic loop ──────────────────────────────────────────────────────────

async def run_agent_loop(
    messages: list[dict],
    model: str | None = None,
    max_iterations: int | None = None,
    session_id: str = "",
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
    deadline = time.monotonic() + cfg.agent_timeout_seconds

    loop_messages = list(messages)
    iteration = 0
    tools_called = 0
    # Track whether we've already fallen back so we don't loop forever
    _on_fallback = (target_model == cfg.fallback_local_model)

    while iteration < max_iter:
        iteration += 1

        # ── Hard timeout check ─────────────────────────────────────────────────
        if time.monotonic() > deadline:
            yield {"type": "token",
                   "token": f"\n\n[Agent timed out after {cfg.agent_timeout_seconds}s]\n\n"}
            break

        # ── Ask the model (streaming with tools) ──────────────────────────────
        # qwen3 emits <think>…</think> reasoning before tool calls or answers.
        # _emit_think_chunk() splits that into "thinking_trace" events (shown in
        # a collapsible UI block) vs "token" events (the real response text).
        # qwen2.5 emits no think tags so the parser is a transparent pass-through.
        tool_calls: list[dict] = []
        streamed_content = False
        streamed_content_text = ""   # real response text (think stripped)
        _think_buf = ""              # lookahead buffer for tag boundary splits
        _in_think  = False           # currently inside <think>…</think>

        # ── Stream with automatic fallback on connectivity errors ─────────────
        # If the primary model is unreachable (Ollama down, model not loaded,
        # network timeout), we switch to cfg.fallback_local_model and inform
        # the user via a token event.  Mid-stream failures (model error after
        # connection) are not retried — they propagate as error events.
        _stream_model = target_model
        while True:
            try:
                async for evt_type, evt_data in _ollama_stream_with_tools(
                    loop_messages, model=_stream_model
                ):
                    if evt_type == "thinking":
                        _think_buf += evt_data
                        _think_buf, _in_think, events = _emit_think_chunk(_think_buf, _in_think)
                        for ev_name, ev_text in events:
                            if ev_text:
                                yield {"type": ev_name, "token": ev_text}
                                if ev_name == "token":
                                    streamed_content_text += ev_text
                                    streamed_content = True
                    elif evt_type == "tool_calls":
                        tool_calls = evt_data
                # Flush any remaining buffer after stream ends
                if _think_buf:
                    ev_name = "thinking_trace" if _in_think else "token"
                    yield {"type": ev_name, "token": _think_buf}
                    if ev_name == "token":
                        streamed_content_text += _think_buf
                        streamed_content = True
                break  # stream completed successfully

            except _OLLAMA_CONNECT_ERRORS as exc:
                fallback = cfg.fallback_local_model
                if _on_fallback or _stream_model == fallback:
                    # Already on fallback — nothing left to try
                    logger.error(
                        "Ollama unreachable on fallback model %s: %s", fallback, exc
                    )
                    yield {"type": "error",
                           "message": f"Ollama unreachable: {exc}. Is Ollama running?"}
                    return
                logger.warning(
                    "Model %s unreachable — switching to fallback %s: %s",
                    _stream_model, fallback, exc,
                )
                yield {
                    "type": "token",
                    "token": (
                        f"\n⚠️  {_stream_model} unavailable — "
                        f"switching to {fallback}…\n\n"
                    ),
                }
                _stream_model = fallback
                _on_fallback = True
                # Reset stream state and retry with fallback model
                tool_calls = []
                streamed_content = False
                streamed_content_text = ""
                _think_buf = ""
                _in_think = False

            except Exception as exc:
                logger.error("Ollama stream error (model=%s): %s", _stream_model, exc)
                yield {"type": "error", "message": f"Ollama request failed: {exc}"}
                return

        # ── No tool calls → streamed content was the final answer ─────────────
        if not tool_calls:
            if not streamed_content:
                # Rare: empty response — retry once with fresh streaming call
                retried = False
                async for token in _ollama_stream_final(loop_messages, model=target_model):
                    yield {"type": "token", "token": token}
                    retried = True
                if not retried:
                    yield {"type": "token", "token": "(no response)"}
            break

        # ── Emit all tool_call events upfront (UI shows pending state) ─────────
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = _parse_args(fn.get("arguments", {}))
            tools_called += 1
            yield {"type": "tool_call", "name": name, "args": args, "iteration": iteration}

        # Append the assistant message with tool_calls
        loop_messages.append({
            "role": "assistant",
            "content": streamed_content_text,
            "tool_calls": tool_calls,
        })

        # ── Execute ALL tools in parallel ──────────────────────────────────────
        results: list[tuple[str, dict, str]] = list(
            await asyncio.gather(
                *[_execute_tool_call(tc, session_id=session_id) for tc in tool_calls],
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

        # Prune old tool messages to protect context window
        loop_messages = _prune_tool_messages(loop_messages)

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
    session_id: str = "",
) -> tuple[str, int, dict | None]:
    """
    Collect all tokens into a string.
    Returns (full_text, tools_called, shell_gate_payload | None).
    Token and thinking events both contribute to full_text.
    """
    tokens: list[str] = []
    tools_called = 0
    shell_gate: dict | None = None

    async for event in run_agent_loop(messages, model=model,
                                      max_iterations=max_iterations,
                                      session_id=session_id):
        etype = event.get("type")
        if etype == "token":
            tokens.append(event["token"])
        elif etype == "thinking_trace":
            pass  # reasoning trace — not part of the final collected answer
        elif etype == "agent_done":
            tools_called = event["tools_called"]
        elif etype == "shell_gate":
            shell_gate = event["payload"]
        elif etype == "error":
            tokens.append(f"\n[Agent error: {event['message']}]")

    return "".join(tokens), tools_called, shell_gate
