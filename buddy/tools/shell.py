"""
Shell tool — every command requires explicit human confirmation.
Banned patterns are checked before the confirmation prompt.

CSRF protection
───────────────
requires_confirmation() issues a one-time token tied to the exact command.
/shell/execute must present the same token; it is deleted on first use.
This prevents another browser tab or cross-origin request from executing
a command that a different session queued.
"""
from __future__ import annotations

import asyncio
import secrets
import subprocess
from typing import Optional

from buddy.config import settings as cfg


class ShellDeniedError(Exception):
    """Raised when a command is blocked before reaching the human gate."""


# ── Pending token store ────────────────────────────────────────────────────────
# Maps one-time token → command string.
# Populated by requires_confirmation(), consumed (and deleted) by
# consume_pending_token() in the execute endpoint.
_pending_tokens: dict[str, str] = {}


def _check_banned(command: str) -> None:
    for pattern in cfg.shell_banned_patterns:
        if pattern in command:
            raise ShellDeniedError(
                f"Command contains banned pattern '{pattern}' and was blocked."
            )


def requires_confirmation(command: str) -> dict:
    """
    Issue a one-time CSRF token bound to this command.
    The API layer shows the command to the user; on approval the frontend
    must pass the token back to /shell/execute.
    """
    _check_banned(command)
    token = secrets.token_hex(16)
    _pending_tokens[token] = command
    return {
        "type": "shell_confirmation",
        "command": command,
        "token": token,
        "message": f"Buddy wants to run:\n\n  {command}\n\nApprove?",
    }


def consume_pending_token(token: str, command: str) -> bool:
    """
    Validate that *token* was issued for *command* and remove it.
    Returns True on success, False if the token is missing or mismatched.
    Each token is single-use: a second call with the same token always returns False.
    """
    stored = _pending_tokens.pop(token, None)
    return stored is not None and stored == command


def execute(command: str, timeout: int = 30) -> str:
    """Run command after human gate has been passed. Returns stdout."""
    _check_banned(command)
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output += f"\n[stderr]: {result.stderr.strip()[:500]}"
    return output or "(no output)"
