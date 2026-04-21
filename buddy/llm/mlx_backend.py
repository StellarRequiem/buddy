"""
MLX inference backend for Apple Silicon.

Talks to an mlx-lm OpenAI-compatible server running locally.
Yields the same event types as the Ollama backend so agent.py
can swap them with zero logic changes.

Quick start
───────────
Install mlx-lm (once):
    pip install mlx-lm

Start the server (pick a free port — buddy default is 7439):
    python -m mlx_lm.server \\
        --model mlx-community/Qwen3-14B-4bit \\
        --draft-model mlx-community/Qwen3-1.7B-4bit \\
        --port 7439 --host 127.0.0.1

Or use the helper script:
    bash scripts/start_mlx_server.sh

Then enable in .env:
    USE_MLX_BACKEND=true
    MLX_MODEL=mlx-community/Qwen3-14B-4bit

Fallback chain when the MLX server is unreachable:
    MLX → Ollama conductor → Ollama fallback_local_model

Tool-calling compatibility note
────────────────────────────────
dflash-mlx (by @Aryagm) currently ships a text-only server — no tool
calling.  mlx-lm's own server supports the full OpenAI tool-calling API
AND can be accelerated with a draft model for speculative decoding.
When dflash-mlx adds tool support this file becomes:
    --server dflash-mlx-openai-server   (one flag change in the start script)
The buddy adapter code is identical either way.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from buddy.config import settings as cfg
from buddy.tools.tool_registry import TOOL_SCHEMAS

logger = logging.getLogger(__name__)


# ── Streaming helpers ──────────────────────────────────────────────────────────

async def mlx_stream_with_tools(
    messages: list[dict],
    model: str,
) -> AsyncGenerator[tuple[str, Any], None]:
    """
    OpenAI-compatible streaming chat completions WITH tool schemas.

    Yields the same event types as _ollama_stream_with_tools:
      ("thinking", str)    — content tokens (may include <think> tags for qwen3)
      ("tool_calls", list) — final accumulated tool calls in Ollama-compat format

    Tool call format emitted:
      [{"function": {"name": str, "arguments": str_or_dict}}, ...]
    _parse_args() in agent.py handles both str and dict arguments.
    """
    url = f"{cfg.mlx_host}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "stream": True,
        "temperature": 0.4,
    }

    # Accumulate streaming tool call deltas across chunks
    # Maps index → {"function": {"name": str, "arguments": str}}
    tool_call_accum: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", url, json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    logger.warning("mlx_stream_with_tools: JSON decode error: %s", exc)
                    continue

                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                finish_reason = choice.get("finish_reason")

                # ── Content tokens (may contain <think> tags) ──────────────
                if content := delta.get("content"):
                    yield ("thinking", content)

                # ── Accumulate tool call fragments ─────────────────────────
                for delta_tc in delta.get("tool_calls") or []:
                    idx = delta_tc.get("index", 0)
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {
                            "function": {"name": "", "arguments": ""}
                        }
                    tc = tool_call_accum[idx]
                    fn = delta_tc.get("function") or {}
                    if fn.get("name"):
                        tc["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        tc["function"]["arguments"] += fn["arguments"]

                # ── Emit complete tool calls on any terminal finish_reason ──
                # Some servers use "tool_calls", others use "stop".
                # We emit whenever finish_reason is set and we have accumulated calls.
                if finish_reason is not None and tool_call_accum:
                    ollama_tcs = [
                        {"function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        }}
                        for _, tc in sorted(tool_call_accum.items())
                    ]
                    logger.debug(
                        "mlx tool_calls: %s",
                        [tc["function"]["name"] for tc in ollama_tcs],
                    )
                    yield ("tool_calls", ollama_tcs)
                    return  # tool calls are the complete response


async def mlx_stream_final(
    messages: list[dict],
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Stream a plain text response (no tools parameter).
    Used for final synthesis after tool loop completes.
    Yields raw token strings identical to _ollama_stream_final.
    """
    url = f"{cfg.mlx_host}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST", url, json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                token = (
                    (chunk.get("choices") or [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if token:
                    yield token


async def mlx_health() -> dict:
    """
    Ping the MLX server health endpoint.
    Returns {"ok": bool, "models": list, "error": str|None}.
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{cfg.mlx_host}/health")
            resp.raise_for_status()
            models_resp = await client.get(f"{cfg.mlx_host}/v1/models")
            models = [m["id"] for m in models_resp.json().get("data", [])]
        return {"ok": True, "models": models, "error": None}
    except Exception as exc:
        return {"ok": False, "models": [], "error": str(exc)}
