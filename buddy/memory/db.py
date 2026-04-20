"""
SQLite schema and connection for Buddy.

Schema changes are managed with a lightweight migration system:
  - Every schema change lives as a numbered entry in _MIGRATIONS.
  - init_db() tracks applied versions in the `schema_migrations` table.
  - On startup, only un-applied migrations run, in order.
  - To add a new column / table: append a new (version, description, sql) tuple.

Tables:
  conversations     -- chat message history
  user_facts        -- persistent facts buddy learns about the user
  tasks             -- queued / in-progress / done task records
  grading_log       -- cus-core grade results for every LLM call
  schema_migrations -- applied migration versions (internal)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from buddy.config import settings


# ── Versioned migrations ───────────────────────────────────────────────────────
# Add new entries at the END. Never edit or reorder existing entries.
# Each entry is (version: int, description: str, sql: str).

_MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema",
        """
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
        """,
    ),
    # ── Add new migrations here -- never edit entries above ────────────────────
    (
        2,
        "tool_calls metrics table",
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL DEFAULT (datetime('now')),
            session_id  TEXT,
            tool_name   TEXT    NOT NULL,
            success     INTEGER NOT NULL DEFAULT 1,
            latency_ms  INTEGER,
            args_summary TEXT,
            result_preview TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);
        CREATE INDEX IF NOT EXISTS idx_tool_calls_ts   ON tool_calls(ts);
        """,
    ),
    (
        3,
        "audit_log table",
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT    NOT NULL DEFAULT (datetime('now')),
            action     TEXT    NOT NULL,
            session_id TEXT    DEFAULT '',
            detail     TEXT    DEFAULT '',
            source_ip  TEXT    DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(ts);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
        """,
    ),
]


def _get_db_path() -> Path:
    return settings.db_path


def init_db() -> None:
    """
    Initialise schema and run any pending migrations.
    Safe to call on every startup -- already-applied migrations are skipped.
    """
    db_path = _get_db_path()
    with sqlite3.connect(db_path) as conn:
        # Bootstrap the migrations tracking table (always safe to run)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                description TEXT,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()

        applied: set[int] = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations")
        }

        for version, description, sql in _MIGRATIONS:
            if version in applied:
                continue
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                (version, description),
            )
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
