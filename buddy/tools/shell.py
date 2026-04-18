"""
Shell tool — every command requires explicit human confirmation.
Banned patterns are checked before the confirmation prompt.
"""
from __future__ import annotations

import asyncio
import subprocess

from buddy.config import settings as cfg


class ShellDeniedError(Exception):
    """Raised when a command is blocked before reaching the human gate."""


def _check_banned(command: str) -> None:
    for pattern in cfg.shell_banned_patterns:
        if pattern in command:
            raise ShellDeniedError(
                f"Command contains banned pattern '{pattern}' and was blocked."
            )


def requires_confirmation(command: str) -> dict:
    """
    Return a confirmation request dict. The API layer shows this to the user
    and waits for approval before calling execute().
    """
    _check_banned(command)
    return {
        "type": "shell_confirmation",
        "command": command,
        "message": f"Buddy wants to run:\n\n  {command}\n\nApprove?",
    }


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
