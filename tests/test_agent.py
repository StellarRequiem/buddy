"""
Tests for the agent loop utilities and tool registry.
No Ollama calls are made — all LLM interactions are mocked.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── agent.py unit tests ────────────────────────────────────────────────────────

from buddy.llm.agent import (
    _truncate_result, _preview, _prune_tool_messages,
    _parse_args, _is_shell_gate, _SHELL_GATE_PREFIX, _MAX_TOOL_RESULT,
)


def test_truncate_result_short():
    assert _truncate_result("hello") == "hello"


def test_truncate_result_long():
    long = "x" * (_MAX_TOOL_RESULT + 500)
    result = _truncate_result(long)
    assert len(result) <= _MAX_TOOL_RESULT + 60   # some room for the truncation note
    assert "truncated" in result


def test_preview_short():
    assert _preview("hi there") == "hi there"


def test_preview_long():
    text = "a" * 200
    preview = _preview(text, max_len=120)
    assert len(preview) <= 124   # 120 + "…"
    assert preview.endswith("…")


def test_parse_args_dict():
    assert _parse_args({"key": "val"}) == {"key": "val"}


def test_parse_args_json_string():
    assert _parse_args('{"key": "val"}') == {"key": "val"}


def test_parse_args_invalid():
    assert _parse_args("not json") == {}
    assert _parse_args(42) == {}


def test_is_shell_gate_positive():
    assert _is_shell_gate(f"{_SHELL_GATE_PREFIX} ls -la") is True


def test_is_shell_gate_negative():
    assert _is_shell_gate("regular tool output") is False


def test_prune_tool_messages_no_prune_needed():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "r1"},
        {"role": "tool", "content": "r2"},
    ]
    pruned = _prune_tool_messages(msgs)
    assert pruned == msgs   # under limit, no change


def test_prune_tool_messages_prunes_oldest():
    from buddy.llm.agent import _MAX_TOOL_MESSAGES
    msgs = (
        [{"role": "system", "content": "sys"}]
        + [{"role": "tool", "content": f"r{i}"} for i in range(_MAX_TOOL_MESSAGES + 4)]
        + [{"role": "user", "content": "final"}]
    )
    pruned = _prune_tool_messages(msgs)
    tool_msgs = [m for m in pruned if m["role"] == "tool"]
    assert len(tool_msgs) <= _MAX_TOOL_MESSAGES
    # System and user messages are never dropped
    assert any(m["role"] == "system" for m in pruned)
    assert any(m["role"] == "user" for m in pruned)


# ── tool_registry.py unit tests ───────────────────────────────────────────────

import pytest


@pytest.mark.asyncio
async def test_execute_tool_unknown():
    from buddy.tools.tool_registry import execute_tool
    result = await execute_tool("nonexistent_tool", {})
    assert "Unknown tool" in result
    assert "nonexistent_tool" in result


@pytest.mark.asyncio
async def test_execute_tool_disabled(monkeypatch):
    from buddy import config
    from buddy.tools.tool_registry import execute_tool
    monkeypatch.setattr(config.settings, "disabled_tools", ["get_datetime"])
    result = await execute_tool("get_datetime", {})
    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_execute_tool_get_datetime():
    from buddy.tools.tool_registry import execute_tool
    result = await execute_tool("get_datetime", {})
    assert "Date" in result or "Time" in result


@pytest.mark.asyncio
async def test_execute_tool_run_python_blocked():
    from buddy.tools.tool_registry import execute_tool
    result = await execute_tool("run_python", {"code": "import os; os.listdir('/')"})
    assert "blocked" in result.lower()


@pytest.mark.asyncio
async def test_execute_tool_run_python_ok():
    from buddy.tools.tool_registry import execute_tool
    result = await execute_tool("run_python", {"code": "print(2 + 2)"})
    assert "4" in result


@pytest.mark.asyncio
async def test_shell_tool_returns_gate_sentinel():
    from buddy.tools.tool_registry import execute_tool
    result = await execute_tool("shell_execute", {"command": "ls"})
    assert result.startswith("[SHELL_GATE_PENDING]")


@pytest.mark.asyncio
async def test_note_write_read(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config.settings, "vault_path", tmp_path)
    from buddy.tools.tool_registry import execute_tool
    # Write
    write_result = await execute_tool("note_write", {
        "title": "test note", "content": "# Hello\n\nworld"
    })
    assert "saved" in write_result.lower() or "test note" in write_result.lower()
    # Read back
    read_result = await execute_tool("note_read", {"title": "test note"})
    assert "Hello" in read_result or "world" in read_result
    # List
    list_result = await execute_tool("note_list", {})
    assert "test" in list_result.lower()


# ── agent loop mock test ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_agent_collect_no_tools(monkeypatch):
    """When model returns plain text on first call (no tool_calls), collect returns it."""
    from buddy.llm.agent import run_agent_collect

    async def _mock_stream_with_tools(messages, model):
        yield ("thinking", "Hello from model")
        # no "tool_calls" event → no tools called

    monkeypatch.setattr("buddy.llm.agent._ollama_stream_with_tools", _mock_stream_with_tools)

    text, tools_called, shell_gate = await run_agent_collect(
        [{"role": "user", "content": "hi"}]
    )
    assert "Hello from model" in text
    assert tools_called == 0
    assert shell_gate is None
