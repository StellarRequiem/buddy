"""
HTTP integration tests — FastAPI TestClient, Ollama fully mocked.

Tests the full request→response stack including routing, session persistence,
agent loop wiring, SSE event format, and admin endpoints.
No Ollama, no Anthropic API, no Chroma calls are made.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


# ── App fixture ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Boot the app with a temp vault so tests don't touch the real DB."""
    tmp = tmp_path_factory.mktemp("vault")

    # Patch settings BEFORE importing the app so all modules see the new paths
    import buddy.config as _cfg
    _cfg.settings.vault_path    = tmp
    _cfg.settings.db_path       = tmp / "buddy.db"
    _cfg.settings.chroma_path   = tmp / "chroma"
    _cfg.settings.use_agent_loop    = True
    _cfg.settings.anthropic_api_key = ""   # disable frontier escalation
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "chroma").mkdir(parents=True, exist_ok=True)

    # Initialise the schema in the temp DB
    from buddy.memory.db import init_db
    init_db()

    from buddy.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Helper — mock the agent loop ───────────────────────────────────────────────

def _patch_agent_collect(text="Hello!", tools=0, gate=None):
    """Patch run_agent_collect to return a fixed response."""
    async def _mock(*args, **kwargs):
        return text, tools, gate
    return patch("buddy.api.chat.run_agent_collect", new=_mock)


def _patch_agent_loop(events):
    """Patch run_agent_loop to yield a fixed sequence of events."""
    async def _mock(*args, **kwargs):
        for e in events:
            yield e
    return patch("buddy.api.chat.run_agent_loop", new=_mock)


# ── POST /chat ─────────────────────────────────────────────────────────────────

def test_chat_basic(client):
    with _patch_agent_collect("The answer is 42."):
        resp = client.post("/chat", json={"message": "what is 6 times 7?"})
    assert resp.status_code == 200
    d = resp.json()
    assert "42" in d["response"]
    assert d["session_id"]
    assert d["tools_called"] == 0


def test_chat_session_persisted(client):
    """Two calls with the same session_id keep history."""
    with _patch_agent_collect("First response."):
        r1 = client.post("/chat", json={"message": "msg1"})
    sid = r1.json()["session_id"]

    with _patch_agent_collect("Second response."):
        r2 = client.post("/chat", json={"message": "msg2", "session_id": sid})
    assert r2.json()["session_id"] == sid

    # History endpoint should have both turns
    hist = client.get(f"/chat/history/{sid}").json()
    roles = [m["role"] for m in hist["messages"]]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 2


def test_chat_tools_called_count(client):
    with _patch_agent_collect("Done.", tools=3):
        resp = client.post("/chat", json={"message": "do something"})
    assert resp.json()["tools_called"] == 3


def test_chat_shell_gate_surfaced(client):
    """Shell gate payload propagates from agent to HTTP response."""
    gate = {"command": "ls -la", "token": "abc123"}
    with _patch_agent_collect("Awaiting approval.", tools=1, gate=gate):
        resp = client.post("/chat", json={"message": "list files"})
    d = resp.json()
    assert d["pending_confirmation"] is not None
    assert d["pending_confirmation"]["command"] == "ls -la"


# ── GET /chat/sessions, /chat/history ─────────────────────────────────────────

def test_sessions_list(client):
    # Create a session first
    with _patch_agent_collect("hi"):
        client.post("/chat", json={"message": "hello"})
    resp = client.get("/chat/sessions")
    assert resp.status_code == 200
    assert isinstance(resp.json()["sessions"], list)
    assert len(resp.json()["sessions"]) >= 1


def test_history_unknown_session(client):
    resp = client.get("/chat/history/nonexistent-session-xyz")
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


# ── POST /chat/stream ─────────────────────────────────────────────────────────

def test_stream_basic_tokens(client):
    """SSE stream delivers token events and a done event."""
    events_seq = [
        {"type": "token", "token": "Hello"},
        {"type": "token", "token": " world"},
        {"type": "agent_done", "iterations": 1, "tools_called": 0},
    ]
    with _patch_agent_loop(events_seq):
        with client.stream("POST", "/chat/stream",
                           json={"message": "hi"}) as resp:
            assert resp.status_code == 200
            raw = resp.read().decode()

    lines = [l for l in raw.split("\n") if l.startswith("data: ")]
    parsed = [json.loads(l[6:]) for l in lines]

    tokens = [p["token"] for p in parsed if "token" in p]
    assert "Hello" in tokens

    done = [p for p in parsed if p.get("done")]
    assert len(done) == 1
    assert done[0]["session_id"]


def test_stream_tool_call_events(client):
    """tool_call and tool_result events are forwarded in the SSE stream."""
    events_seq = [
        {"type": "tool_call",   "name": "get_datetime", "args": {}, "iteration": 1},
        {"type": "tool_result", "name": "get_datetime", "preview": "Monday", "full": "Monday", "iteration": 1},
        {"type": "token",       "token": "It is Monday."},
        {"type": "agent_done",  "iterations": 1, "tools_called": 1},
    ]
    with _patch_agent_loop(events_seq):
        with client.stream("POST", "/chat/stream",
                           json={"message": "what day is it?"}) as resp:
            raw = resp.read().decode()

    lines  = [l for l in raw.split("\n") if l.startswith("data: ")]
    parsed = [json.loads(l[6:]) for l in lines]

    tool_calls   = [p for p in parsed if p.get("type") == "tool_call"]
    tool_results = [p for p in parsed if p.get("type") == "tool_result"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "get_datetime"
    assert len(tool_results) == 1


def test_stream_thinking_trace_not_in_done_text(client):
    """thinking_trace events are forwarded but NOT included in done session text."""
    events_seq = [
        {"type": "thinking_trace", "token": "I should check the time..."},
        {"type": "token",          "token": "It is 3pm."},
        {"type": "agent_done",     "iterations": 1, "tools_called": 0},
    ]
    with _patch_agent_loop(events_seq):
        with client.stream("POST", "/chat/stream",
                           json={"message": "what time?"}) as resp:
            raw = resp.read().decode()

    lines  = [l for l in raw.split("\n") if l.startswith("data: ")]
    parsed = [json.loads(l[6:]) for l in lines]

    # thinking_trace forwarded to UI
    traces = [p for p in parsed if p.get("type") == "thinking_trace"]
    assert len(traces) == 1

    # Verify session history only contains the real response, not the trace
    done   = next(p for p in parsed if p.get("done"))
    sid    = done["session_id"]
    hist   = client.get(f"/chat/history/{sid}").json()
    assistant_msgs = [m for m in hist["messages"] if m["role"] == "assistant"]
    assert assistant_msgs
    assert "I should check" not in assistant_msgs[-1]["content"]
    assert "3pm" in assistant_msgs[-1]["content"]


# ── GET /chat/export ──────────────────────────────────────────────────────────

def test_export_session_markdown(client):
    """Export endpoint returns a markdown attachment."""
    with _patch_agent_collect("Export test response."):
        r = client.post("/chat", json={"message": "export test"})
    sid = r.json()["session_id"]

    exp = client.get(f"/chat/export/{sid}")
    assert exp.status_code == 200
    assert "markdown" in exp.headers.get("content-type", "")
    assert "attachment" in exp.headers.get("content-disposition", "")
    assert "Export test response." in exp.text


def test_export_unknown_session_404(client):
    resp = client.get("/chat/export/definitely-does-not-exist")
    assert resp.status_code == 404


# ── Admin endpoints ────────────────────────────────────────────────────────────

def test_admin_status(client):
    resp = client.get("/admin/status")
    assert resp.status_code == 200
    d = resp.json()
    assert "test_mode" in d
    assert "local_model" in d


def test_admin_config(client):
    resp = client.get("/admin/config")
    assert resp.status_code == 200
    d = resp.json()
    assert "conductor_model" in d
    assert d["anthropic_api_key"] in ("***", "(not set)")   # redacted


def test_admin_tool_toggle(client):
    """Disable then re-enable a tool; verify disabled_tools list updates."""
    import buddy.config as _cfg

    # Disable
    r1 = client.post("/admin/tools/get_datetime/toggle",
                     json={"disabled": True})
    assert r1.status_code == 200
    assert r1.json()["disabled"] is True
    assert "get_datetime" in _cfg.settings.disabled_tools

    # Re-enable
    r2 = client.post("/admin/tools/get_datetime/toggle",
                     json={"disabled": False})
    assert r2.status_code == 200
    assert r2.json()["disabled"] is False
    assert "get_datetime" not in _cfg.settings.disabled_tools


def test_admin_tool_toggle_unknown(client):
    resp = client.post("/admin/tools/nonexistent_tool/toggle",
                       json={"disabled": True})
    assert resp.status_code == 404


def test_admin_tool_test_get_datetime(client):
    """Tool test runner executes get_datetime and returns a result."""
    resp = client.post("/admin/tools/test",
                       json={"tool_name": "get_datetime", "args": {}})
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert "Date" in d["result"] or "Time" in d["result"]
    assert d["elapsed_ms"] >= 0


def test_admin_tool_test_unknown_tool(client):
    resp = client.post("/admin/tools/test",
                       json={"tool_name": "does_not_exist", "args": {}})
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is False
    assert "Unknown tool" in d["result"] or "does_not_exist" in d["result"]


# ── Memory endpoints ───────────────────────────────────────────────────────────

def test_memory_tools_catalogue(client):
    resp = client.get("/memory/tools")
    assert resp.status_code == 200
    d = resp.json()
    assert d["count"] >= 20
    names = [t["name"] for t in d["tools"]]
    assert "forest_status"    in names
    assert "forest_incidents" in names
    assert "forest_scan"      in names
    assert "get_datetime"     in names


def test_memory_facts_roundtrip(client):
    r1 = client.post("/memory/facts", json={"key": "test_key", "value": "test_val"})
    assert r1.status_code == 200
    r2 = client.get("/memory/facts")
    assert "test_key" in r2.json()["facts"]
    assert r2.json()["facts"]["test_key"] == "test_val"
