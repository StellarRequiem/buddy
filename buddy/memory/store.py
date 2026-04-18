"""
CRUD operations on the SQLite store.
All public functions take/return plain dicts — no ORM leakage.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from buddy.memory.db import get_conn


# ── Conversations ──────────────────────────────────────────────────────────────

def append_message(session_id: str, role: str, content: str, model: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (session_id, role, content, model) VALUES (?,?,?,?)",
            (session_id, role, content, model),
        )
        return cur.lastrowid


def get_history(session_id: str, limit: int = 40) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content, model, ts FROM conversations "
            "WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def list_sessions() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM conversations ORDER BY MIN(id)"
        ).fetchall()
    return [r["session_id"] for r in rows]


# ── User facts ─────────────────────────────────────────────────────────────────

def upsert_fact(key: str, value: str, source: str = "inferred") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_facts (key, value, source) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source, "
            "ts=datetime('now')",
            (key, value, source),
        )


def get_facts() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM user_facts").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ── Tasks ──────────────────────────────────────────────────────────────────────

def create_task(title: str, metadata: dict | None = None) -> str:
    task_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, title, metadata) VALUES (?,?,?)",
            (task_id, title, json.dumps(metadata or {})),
        )
    return task_id


def update_task(task_id: str, status: str, result: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, result=?, updated_at=datetime('now') WHERE id=?",
            (status, result, task_id),
        )


def list_tasks(status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


# ── Grading log ────────────────────────────────────────────────────────────────

def log_grade(session_id: str, call_type: str, model: str,
              composite_score: float, passed: bool, detail: Any = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO grading_log (session_id, call_type, model, composite_score, passed, detail) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, call_type, model, composite_score, int(passed),
             json.dumps(detail) if detail else None),
        )
