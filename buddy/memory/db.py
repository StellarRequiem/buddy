"""
SQLite schema and connection for Buddy.
Tables:
  conversations  — chat message history
  user_facts     — persistent facts buddy learns about the user
  tasks          — queued / in-progress / done task records
  grading_log    — cus-core grade results for every LLM call
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

from buddy.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    role        TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT    NOT NULL,
    model       TEXT,
    ts          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    source      TEXT,
    ts          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT    PRIMARY KEY,
    title       TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','running','done','failed')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    result      TEXT,
    metadata    TEXT    DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS grading_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    call_type       TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    composite_score REAL,
    passed          INTEGER,
    ts              TEXT    NOT NULL DEFAULT (datetime('now')),
    detail          TEXT
);

CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_grading_ts   ON grading_log(ts);
"""


def _get_db_path() -> Path:
    return settings.db_path


def init_db() -> None:
    """Create tables if they don't exist."""
    with sqlite3.connect(_get_db_path()) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection with row_factory set for dict-style access."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
