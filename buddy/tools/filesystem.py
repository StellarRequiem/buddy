"""
Filesystem tool — scoped to ~/BuddyVault/ and an explicit allow-list.
Reads only. No writes. Path traversal blocked.
"""
from __future__ import annotations

from pathlib import Path

from buddy.config import settings as cfg

_MAX_BYTES = 50_000  # 50KB cap per read


def _resolve_allowed(path_str: str) -> Path:
    """Resolve path and verify it's within an allowed root. Raises ValueError if not."""
    p = Path(path_str).expanduser().resolve()

    # Always allow BuddyVault
    vault = cfg.vault_path.resolve()
    if str(p).startswith(str(vault)):
        return p

    # Check allow-list
    for allowed in cfg.allowed_read_paths:
        allowed_resolved = Path(allowed).expanduser().resolve()
        if str(p).startswith(str(allowed_resolved)) or p == allowed_resolved:
            return p

    raise ValueError(
        f"Path '{p}' is outside allowed scope. "
        f"Allowed: ~/BuddyVault/ and {cfg.allowed_read_paths}"
    )


def read_file(path_str: str) -> str:
    """Read a file and return its contents (truncated at 50KB)."""
    p = _resolve_allowed(path_str)

    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if not p.is_file():
        raise ValueError(f"Not a file: {p}")

    raw = p.read_bytes()[:_MAX_BYTES]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    truncated = len(p.read_bytes()) > _MAX_BYTES
    if truncated:
        text += f"\n\n[...truncated at {_MAX_BYTES} bytes]"

    return text


def list_dir(path_str: str) -> list[str]:
    """List directory contents (names only, no recursive walk)."""
    p = _resolve_allowed(path_str)
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    return sorted(item.name for item in p.iterdir())
