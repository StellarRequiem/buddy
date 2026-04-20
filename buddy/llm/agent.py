"""
Native tool-calling agentic loop — the Apex Predator core.

Architecture:
  1. Build messages with full tool schemas (TOOL_SCHEMAS → Ollama `tools` param)
  2. POST /api/chat  — model returns either text OR tool_calls[]
  3. For each tool call:
       a. Yield {"type": "tool_call", "name": ..., "args": ...}  (SSE)
       b. Execute via execute_tool()
       c. Yield {"type": "tool_result", "name": ..., "preview": ...}  (SSE)
       d. Append tool result message and loop
  4. Shell gate: if result starts with [SHELL_GATE_PENDING], stop loop
     and surface the confirmation payload to the caller
  5. When model emits text (no more tool_calls): stream final tokens
  6. Hard stop at max_agent_iterations to prevent runaway loops

Falls back to legacy local_chat_stream() when:
  - cfg.use_agent_loop = False
  - Model doesn't support tool calling (no tool_calls in response)
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from buddy.config import settings as cfg
from buddy.tools.tool_registry import TOOL_SCHEMAS, execute_tool, _TOOL_MAP
from buddy.tools.shell import requires_confirmation

logger = logging.getLogger(__name__)

# ── SSE event helpers ──────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _preview(text: str, max_len: int = 200) -> str:
    """Short preview of tool output for the activity panel."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


# ── Shell gate sentinel ────────────────────────────────────────────────────────

_SHELL_GATE_PREFIX = "[SHELL_GATE_PENDING]"


def _is_shell_gate(result: str) -> bool:
    return result.startswith(_SHELL_GATE_PREFIX)


# ── Ollama tool-calling request ────────────────────────────────────────────────

async def _ollama_tool_call(
    messages: list[dict],
    model: str,
    stream: bool = False,
) -> dict:
    """
    POST /api/chat with tool schemas.
    Returns the parsed JSON response dict (message object).
    When stream=True the model MUST be called with stream=False here —
    tool-calling mode is always non-streaming (we stream manually after).
    """
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(
            f"{cfg.ollama_host}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "tools": TOOL_SCHEMAS,
                "stream": False,   # tool-calling phase is always sync
                "options": {"temperature": 0.4},  # lower temp for tool reasoning
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _ollama_stream_final(
    messages: list[dict],
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream the final text response once tool use is complete.
    Called with no tools so model just generates text.
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


# ── Main agentic loop ──────────────────────────────────────────────────────────

async def run_agent_loop(
    messages: list[dict],
    model: str | None = None,
    max_iterations: int | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields SSE-ready dicts:
      {"type": "tool_call",   "name": str, "args": dict, "iteration": int}
      {"type": "tool_result", "name": str, "preview": str, "iteration": int}
      {"type": "token",       "token": str}
      {"type": "shell_gate",  "payload": dict}   — stops the loop
      {"type": "agent_done",  "iterations": int, "tools_called": int}
      {"type": "error",       "message": str}

    The caller is responsible for converting these to SSE data: ... lines.
    """
    target_model = model or cfg.conductor_model
    max_iter = max_iterations if max_iterations is not None else cfg.max_agent_iterations

    loop_messages = list(messages)
    iteration = 0
    tools_called = 0
    shell_gate_payload: dict | None = None

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
        content: str = msg.get("content") or ""

        # ── No tool calls → model is done; stream final text ──────────────────
        if not tool_calls:
            if content:
                # Deliver the already-buffered content as tokens
                # (model returned content in non-streaming mode)
                for token in content.split(" "):
                    # re-split so UI receives incremental chunks
                    yield {"type": "token", "token": token + " "}
            else:
                # Edge: empty response — stream with a fresh call (no tools)
                async for token in _ollama_stream_final(loop_messages, model=target_model):
                    yield {"type": "token", "token": token}
            break

        # ── Execute each tool call ─────────────────────────────────────────────
        # Append the assistant message containing tool_calls first
        loop_messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc.get("function", {})
            name: str = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args: dict = raw_args if isinstance(raw_args, dict) else {}

            tools_called += 1
            yield {"type": "tool_call", "name": name, "args": args, "iteration": iteration}

            # ── Execute ────────────────────────────────────────────────────────
            try:
                result_text = await execute_tool(name, args)
            except Exception as exc:
                result_text = f"[Tool error: {exc}]"

            # ── Shell gate check ───────────────────────────────────────────────
            if _is_shell_gate(result_text):
                # The shell tool returns [SHELL_GATE_PENDING] <command>.
                # We call requires_confirmation() here to create the CSRF token
                # and confirmation payload that the UI will use.
                raw_command = result_text[len(_SHELL_GATE_PREFIX):].strip()
                try:
                    shell_gate_payload = requires_confirmation(raw_command)
                except Exception as exc:
                    shell_gate_payload = {"command": raw_command, "token": "",
                                          "error": str(exc)}
                yield {"type": "shell_gate", "payload": shell_gate_payload}
                yield {"type": "agent_done", "iterations": iteration,
                       "tools_called": tools_called}
                return

            preview = _preview(result_text)
            yield {"type": "tool_result", "name": name, "preview": preview,
                   "iteration": iteration}

            # Inject result as tool message for next iteration
            loop_messages.append({
                "role": "tool",
                "content": result_text,
            })

    else:
        # Hit max iterations without a final text response
        # Do one last streaming call to generate a summary
        yield {"type": "token", "token": "\n\n[Reached max tool iterations — summarising]\n\n"}
        async for token in _ollama_stream_final(loop_messages, model=target_model):
            yield {"type": "token", "token": token}

    yield {"type": "agent_done", "iterations": iteration, "tools_called": tools_called}


# ── Convenience: collect full text from agent loop ─────────────────────────────

async def run_agent_collect(
    messages: list[dict],
    model: str | None = None,
    max_iterations: int | None = None,
) -> tuple[str, int, dict | None]:
    """
    Non-streaming version: collect all tokens, return (full_text, tools_called, shell_gate_payload).
    Used by the non-streaming POST /chat endpoint.
    """
    tokens: list[str] = []
    tools_called = 0
    shell_gate: dict | None = None

    async for event in run_agent_loop(messages, model=model, max_iterations=max_iterations):
        if event["type"] == "token":
            tokens.append(event["token"])
        elif event["type"] == "agent_done":
            tools_called = event["tools_called"]
        elif event["type"] == "shell_gate":
            shell_gate = event["payload"]
        elif event["type"] == "error":
            tokens.append(f"\n[Agent error: {event['message']}]")

    return "".join(tokens), tools_called, shell_gate
