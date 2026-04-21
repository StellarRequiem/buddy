"""
Shell tool — every command requires explicit human confirmation.
Banned patterns are checked before the confirmation prompt.

CSRF protection
───────────────
requires_confirmation() issues a one-time token tied to the exact command
and stores it in SQLite with a 10-minute TTL.  This means:
  - Tokens survive server restarts (no more lost approvals on process death)
  - Tokens work across multiple uvicorn workers (shared DB, not in-process dict)
  - Expired tokens are rejected and cleaned up at startup
  - Each token is single-use: a second call with the same token returns False

Banned-pattern matching
───────────────────────
Patterns are matched case-insensitively and with flexible whitespace so
"rm  -rf" (double space) and "RM -RF" are caught alongside "rm -rf".
"""
from __future__ import annotations

import logging
import re
import secrets
import subprocess
from datetime import datetime, timedelta, timezone

from buddy.config import settings as cfg

logger = logging.getLogger(__name__)

# Token TTL — how long a shell confirmation stays valid after issue
_TOKEN_TTL_MINUTES = 10


class ShellDeniedError(Exception):
    """Raised when a command is blocked before reaching the human gate."""


# ── Banned-pattern helpers ─────────────────────────────────────────────────────

def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """
    Convert a plain-text banned pattern to a compiled regex.
    Spaces in the pattern become \\s+ (flexible whitespace).
    Matching is always case-insensitive.
    """
    escaped = re.escape(pattern)
    flexible = escaped.replace(r"\ ", r"\s+")
    return re.compile(flexible, re.IGNORECASE)


def _check_banned(command: str) -> None:
    for pattern in cfg.shell_banned_patterns:
        if _pattern_to_regex(pattern).search(command):
            raise ShellDeniedError(
                f"Command contains banned pattern '{pattern}' and was blocked."
            )


# ── SQLite-backed token store ──────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_utc() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=_TOKEN_TTL_MINUTES)).isoformat()


def cleanup_expired_shell_tokens() -> int:
    """
    Delete all expired shell tokens.
    Called at startup and can be called from admin endpoints.
    Returns the number of tokens removed.
    """
    from buddy.memory.db import get_conn
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM shell_tokens WHERE expires_at < ?", (_now_utc(),)
            )
            removed = cur.rowcount
        if removed:
            logger.info("Cleaned up %d expired shell token(s)", removed)
        return removed
    except Exception as exc:
        logger.warning("cleanup_expired_shell_tokens failed: %s", exc)
        return 0


def requires_confirmation(command: str, session_id: str = "") -> dict:
    """
    Issue a one-time CSRF token bound to this command, persisted in SQLite.
    The API layer shows the command to the user; on approval the frontend
    must pass the token back to /shell/execute.
    Token expires after _TOKEN_TTL_MINUTES minutes.
    """
    _check_banned(command)
    from buddy.memory.db import get_conn
    token = secrets.token_hex(16)
    expires = _expiry_utc()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO shell_tokens (token, command, session_id, expires_at) "
                "VALUES (?,?,?,?)",
                (token, command, session_id, expires),
            )
    except Exception as exc:
        logger.error("requires_confirmation: failed to persist token: %s", exc)
        raise
    logger.debug("shell_token issued: cmd=%r ttl=%dm", command[:60], _TOKEN_TTL_MINUTES)
    return {
        "type": "shell_confirmation",
        "command": command,
        "token": token,
        "expires_at": expires,
        "message": f"Buddy wants to run:\n\n  {command}\n\nApprove?",
    }


def consume_pending_token(token: str, command: str) -> bool:
    """
    Validate that *token* was issued for *command* and remove it.
    Returns True on success, False if the token is missing, expired, or mismatched.
    Each token is single-use: a second call with the same token always returns False.
    Works across multiple workers and server restarts.
    """
    from buddy.memory.db import get_conn
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT command, expires_at FROM shell_tokens WHERE token=?",
                (token,),
            ).fetchone()
            if row is None:
                logger.warning("consume_pending_token: token not found (token=%s…)", token[:8])
                return False
            # Always delete — even if expired or mismatched (single-use guarantee)
            conn.execute("DELETE FROM shell_tokens WHERE token=?", (token,))
    except Exception as exc:
        logger.error("consume_pending_token: DB error: %s", exc)
        return False

    if row["expires_at"] < _now_utc():
        logger.warning(
            "consume_pending_token: token expired (token=%s… cmd=%r)",
            token[:8], row["command"][:60],
        )
        return False

    if row["command"] != command:
        logger.warning(
            "consume_pending_token: command mismatch (token=%s… "
            "expected=%r got=%r)",
            token[:8], row["command"][:60], command[:60],
        )
        return False

    return True


# ── Executor ───────────────────────────────────────────────────────────────────

def execute(command: str, timeout: int = 30) -> str:
    """Run command after human gate has been passed. Returns stdout + stderr."""
    _check_banned(command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("shell_execute timed out after %ds: cmd=%r", timeout, command[:80])
        return f"[Command timed out after {timeout}s]"

    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output += f"\n[stderr]: {result.stderr.strip()[:500]}"
    return output or "(no output)"
